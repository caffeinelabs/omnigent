/**
 * Loops as strips: one line per loop (implement, review, verify, demo, CI)
 * with its attempts or rounds (`r0 ✗ r1 ✗ r2 ✗`, `#1 3 blocking → #2 ✓`),
 * its state, a budget meter, the last reason, the decision it waits on and
 * the remedies (the loop decision's answers, else Switch vendor and Fresh
 * restart on the task). The PR page, the project's Loops tab and the Agent
 * page's Task & loop tab all draw it.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RepeatIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey } from "@/hooks/useFactory";
import { type DecisionView, type FactorySnapshot, answerDecision } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { InterventionDialog } from "../agents/InterventionDialog";
import type { InterventionKind, InterventionTarget } from "../agents/interventionModel";
import { controlErrorMessage } from "../controls/controlModel";
import { TONE_CHIP, type FactoryTone, formatUsd, shortSha } from "../factoryFormat";
import { agentPageHref, alertPageHref } from "../factoryRoutes";
import { answerBlockedReason } from "../decisions/DecisionCard";
import { LeverGated, ReasonGated, useViewer, useViewerCan } from "../shell/viewerCan";
import { type Attempt, type LoopLine, budgetShare, isBadState, stateText } from "./loopStripModel";

const STATE_TONE: Record<LoopLine["state"], FactoryTone> = {
  running: "active",
  converged: "pass",
  exhausted: "fail",
  thrashing: "fail",
  stalled: "warning",
  dry: "warning",
  escalated: "warning",
  cancelled: "muted",
  superseded: "muted",
};

const OUTCOME: Record<string, { mark: string; tone: string }> = {
  ok: { mark: "✓", tone: "text-success" },
  fail: { mark: "✗", tone: "text-destructive" },
  exhausted: { mark: "✗", tone: "text-destructive" },
  timeout: { mark: "✗", tone: "text-destructive" },
  open: { mark: "◐", tone: "text-session-active" },
  superseded: { mark: "↷", tone: "text-muted-foreground" },
  abandoned: { mark: "–", tone: "text-muted-foreground" },
  cancelled: { mark: "–", tone: "text-muted-foreground" },
};

/** A short word of an attempt's verdict: `CI`, `CHANGES · 3 blocking`, `PASS`. */
export function verdictWord(
  attempt: Pick<Attempt, "verdict" | "blocking" | "outcome">,
  kind: string,
): string | null {
  const verdict = attempt.verdict?.replace(/\s*fail$/i, "").trim() || null;
  if (kind === "review" && attempt.blocking != null && attempt.outcome !== "ok")
    return [verdict, `${attempt.blocking} blocking`].filter(Boolean).join(" · ");
  return verdict;
}

function AttemptChip({
  attempt,
  kind,
  factoryId,
}: {
  attempt: Attempt;
  kind: string;
  factoryId: string;
}) {
  const outcome = OUTCOME[attempt.outcome] ?? OUTCOME.open!;
  const word = verdictWord(attempt, kind);
  const body = (
    <>
      <span className="font-mono">{attempt.label}</span>
      <span aria-hidden className={outcome.tone}>
        {outcome.mark}
      </span>
      {word && <span className="max-w-36 truncate text-muted-foreground">{word}</span>}
      {attempt.head && kind !== "implement" && (
        <span className="font-mono text-muted-foreground max-sm:hidden">
          @{shortSha(attempt.head)}
        </span>
      )}
    </>
  );
  const className =
    "inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-border bg-background px-1.5 text-xs";
  const title = [attempt.label, attempt.verdict, attempt.reason].filter(Boolean).join(" · ");
  return attempt.sessionId ? (
    <Link
      to={agentPageHref(
        factoryId,
        attempt.sessionId,
        attempt.startedAt != null ? { at: attempt.startedAt } : {},
      )}
      componentId="factory.loop.attempt"
      data-testid={`loop-attempt-${attempt.label}`}
      data-outcome={attempt.outcome}
      title={title}
      className={cn(className, "hover:bg-muted")}
    >
      {body}
    </Link>
  ) : (
    <span
      data-testid={`loop-attempt-${attempt.label}`}
      data-outcome={attempt.outcome}
      title={title}
      className={className}
    >
      {body}
    </span>
  );
}

/** The budget meter: ten cells, filled to the share used. */
export function BudgetMeter({
  share,
  tone,
  label,
}: {
  share: number;
  tone: FactoryTone;
  label: string;
}) {
  const cells = 10;
  const filled = Math.round(share * cells);
  return (
    <span
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(share * 100)}
      className="inline-flex shrink-0 gap-px"
      title={label}
    >
      {Array.from({ length: cells }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-2.5 w-1 rounded-[1px]",
            i < filled
              ? tone === "fail"
                ? "bg-destructive"
                : tone === "warning"
                  ? "bg-warning"
                  : tone === "pass"
                    ? "bg-success"
                    : "bg-session-active"
              : "bg-muted",
          )}
        />
      ))}
    </span>
  );
}

function decisionOf(snapshot: FactorySnapshot, line: LoopLine): DecisionView | null {
  const id = line.loop.decision_id;
  if (!id) return null;
  return snapshot.decisions.find((d) => d.id === id && d.status === "open") ?? null;
}

function Remedies({
  snapshot,
  line,
  onIntervene,
}: {
  snapshot: FactorySnapshot;
  line: LoopLine;
  onIntervene: (kind: InterventionKind, target: InterventionTarget) => void;
}) {
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const { can } = useViewerCan();
  const [busy, setBusy] = useState<string | null>(null);
  const decision = decisionOf(snapshot, line);
  const fid = snapshot.factory.id;
  if (decision) {
    const options = decision.options.filter((o) => o.action && o.action !== "dismiss").slice(0, 3);
    // Who may answer: the decision's assignee or claimant (money goes to owners), like its card.
    const blocked = answerBlockedReason(decision, can, viewer?.user_id ?? null);
    return (
      <span
        data-testid={`loop-remedies-${line.loop.kind}`}
        className="flex flex-wrap items-center gap-1"
      >
        {options.map((option) => (
          <ReasonGated key={option.id} reason={blocked} testId="answer">
            <Button
              size="xs"
              variant={option.recommended ? "secondary" : "outline"}
              loading={busy === option.id}
              data-testid={`loop-remedy-${option.id}`}
              componentId={`factory.loop.remedy_${option.action}`}
              onClick={() => {
                setBusy(option.id);
                answerDecision(decision.id, { option_id: option.id })
                  .then(
                    () => showToast(`${decision.ref}: ${option.label}`),
                    (error: unknown) => showToast(`${decision.ref}: ${controlErrorMessage(error)}`),
                  )
                  .finally(() => {
                    setBusy(null);
                    void queryClient.invalidateQueries({ queryKey: factoryQueryKey(fid) });
                  });
              }}
            >
              {option.label}
              {option.recommended ? " ★" : ""}
            </Button>
          </ReasonGated>
        ))}
      </span>
    );
  }
  if (!isBadState(line.state) || line.loop.kind !== "implement" || !line.loop.task_ref) return null;
  const pl = snapshot.projects.find((p) => p.slug === line.loop.project_slug)?.pl;
  if (!pl) return null;
  const target: InterventionTarget = {
    sessionId: pl.session_id,
    title: pl.title,
    role: pl.role,
    project: line.loop.project_slug,
    taskRef: line.loop.task_ref,
  };
  return (
    <span
      data-testid={`loop-remedies-${line.loop.kind}`}
      className="flex flex-wrap items-center gap-1"
    >
      <LeverGated lever="intervene">
        <Button
          size="xs"
          variant="outline"
          onClick={() => onIntervene("reassign", target)}
          componentId="factory.loop.switch"
          data-testid="loop-remedy-switch"
        >
          Switch vendor
        </Button>
      </LeverGated>
      <LeverGated lever="intervene">
        <Button
          size="xs"
          variant="outline"
          onClick={() => onIntervene("redispatch", target)}
          componentId="factory.loop.fresh"
          data-testid="loop-remedy-fresh"
        >
          Fresh restart
        </Button>
      </LeverGated>
    </span>
  );
}

function subjectLabel(line: LoopLine): string | null {
  const { loop } = line;
  if (loop.kind === "implement") return loop.task_ref;
  if (loop.kind === "plan") return loop.project_slug ? `#${loop.project_slug}` : null;
  return loop.pr_number != null ? `!${loop.pr_number}` : null;
}

function LineView({
  snapshot,
  line,
  showSubject,
  onIntervene,
}: {
  snapshot: FactorySnapshot;
  line: LoopLine;
  showSubject: boolean;
  onIntervene: (kind: InterventionKind, target: InterventionTarget) => void;
}) {
  const fid = snapshot.factory.id;
  const tone = STATE_TONE[line.state];
  const share = budgetShare(line);
  const subject = showSubject ? subjectLabel(line) : null;
  const decision = decisionOf(snapshot, line);
  return (
    <li
      data-testid={`loop-line-${line.loop.kind}`}
      data-state={line.state}
      className="grid min-w-0 grid-cols-1 gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0 md:grid-cols-[6.5rem_minmax(0,1fr)_minmax(0,auto)]"
    >
      <span className="flex min-w-0 items-baseline gap-1.5 text-sm font-medium">
        {line.loop.kind}
        {subject && (
          <span className="truncate font-mono text-xs font-normal text-muted-foreground">
            {subject}
          </span>
        )}
      </span>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex min-w-0 flex-wrap items-center gap-1">
          {line.attempts.length === 0 ? (
            <span className="text-xs text-muted-foreground">no iteration yet</span>
          ) : (
            line.attempts.map((attempt, i) => (
              <span
                key={`${attempt.n}-${attempt.label}`}
                className="inline-flex items-center gap-1"
              >
                {i > 0 && (
                  <span aria-hidden className="text-muted-foreground/60">
                    →
                  </span>
                )}
                <AttemptChip attempt={attempt} kind={line.loop.kind} factoryId={fid} />
              </span>
            ))
          )}
          {line.flake && (
            <Link
              to={alertPageHref(fid, line.flake.ref ?? line.flake.id)}
              componentId="factory.loop.flake"
              data-testid="loop-flake"
              className={cn(
                "inline-flex h-6 items-center rounded-md px-1.5 text-xs hover:underline",
                TONE_CHIP.warning,
              )}
              title={line.flake.summary}
            >
              flake suspected · {line.flake.ref}
            </Link>
          )}
        </span>
        {line.lastReason && (
          <span className="min-w-0 text-xs text-muted-foreground">
            last: <span className="break-words text-foreground/80">“{line.lastReason}”</span>
          </span>
        )}
        {(decision || isBadState(line.state)) && (
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {decision && (
              <span className="text-xs text-muted-foreground">
                {decision.ref} → {decision.assignee ?? "operators"}
              </span>
            )}
            <Remedies snapshot={snapshot} line={line} onIntervene={onIntervene} />
          </span>
        )}
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 md:justify-end md:self-start">
        <span
          className={cn(
            "inline-flex h-5 shrink-0 items-center rounded-sm px-1.5 font-mono text-xs",
            TONE_CHIP[tone],
          )}
          data-testid={`loop-state-${line.loop.kind}`}
        >
          {stateText(line)}
        </span>
        {line.headNote && (
          <span className="text-xs text-muted-foreground tabular-nums">{line.headNote}</span>
        )}
        {line.usd && (
          <span className="font-mono text-xs tabular-nums">
            {formatUsd(line.usd.spent)}
            {line.usd.limit != null ? `/${formatUsd(line.usd.limit)}` : ""}
          </span>
        )}
        {share != null && (
          <BudgetMeter
            share={share}
            tone={tone}
            label={`${Math.round(share * 100)}% of the budget used`}
          />
        )}
      </span>
    </li>
  );
}

/** The strip of `lines`; `showSubject` labels each with its task or PR (the project tab). */
export function LoopStrip({
  snapshot,
  lines,
  showSubject = false,
  testId = "loop-strip",
  title = "Loops",
}: {
  snapshot: FactorySnapshot;
  lines: LoopLine[];
  showSubject?: boolean;
  testId?: string;
  title?: string | null;
}) {
  const [acting, setActing] = useState<{
    kind: InterventionKind;
    target: InterventionTarget;
  } | null>(null);
  return (
    <section
      aria-label={title ?? "Loops"}
      data-testid={testId}
      className="flex min-w-0 flex-col gap-2"
    >
      {title && (
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold">
          <RepeatIcon aria-hidden className="size-3.5 text-muted-foreground" />
          {title}
        </h2>
      )}
      <ul className="flex min-w-0 flex-col divide-y divide-border">
        {lines.map((line) => (
          <LineView
            key={line.loop.id}
            snapshot={snapshot}
            line={line}
            showSubject={showSubject}
            onIntervene={(kind, target) => setActing({ kind, target })}
          />
        ))}
      </ul>
      <InterventionDialog
        factoryId={snapshot.factory.id}
        kind={acting?.kind ?? null}
        targets={acting ? [acting.target] : []}
        onOpenChange={(open) => !open && setActing(null)}
      />
    </section>
  );
}
