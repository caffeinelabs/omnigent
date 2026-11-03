/**
 * "Running trials": every trial running now, with the block it holds (no
 * second trial runs there), how far it is, what it promised, its guards
 * against the baseline, and Keep now / Revert for owners.
 */

import { FlaskConicalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DefinitionResponse, ProposalView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { steerPageHref } from "../factoryRoutes";
import { effectText, revertToast, trialProgress } from "../improvements/improvementsModel";
import { ScorecardLine } from "../improvements/Scorecard";
import { useDecideTrial, useProposalScorecard } from "../insights/learnQueries";
import { useFactoryToast } from "../ui/Toast";
import { useSteer } from "./SteerContext";
import { proposerName } from "./steerModel";

function blocksOf(definition: DefinitionResponse, ref: string): string[] {
  return definition.trial_blocks?.find((t) => t.proposal === ref)?.blocks ?? [];
}

function TrialRow({ trial }: { trial: ProposalView }) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const decide = useDecideTrial(steer.factoryId);
  const card = useProposalScorecard(steer.factoryId, trial.ref);
  const progress = trialProgress(trial, steer.nowS);
  const blocks = blocksOf(steer.definition, trial.ref);
  const promise = effectText(trial);
  const days = progress
    ? progress.totalH >= 48
      ? `day ${Math.min(Math.floor(progress.elapsedH / 24) + 1, Math.round(progress.totalH / 24))} of ${Math.round(progress.totalH / 24)}`
      : `${progress.elapsedH} h of ${progress.totalH} h`
    : null;
  const act = (action: "keep" | "revert") =>
    decide.mutate(
      { ref: trial.ref, action },
      {
        onSuccess: ({ proposal }) =>
          toast.show({
            message: action === "keep" ? `Kept ${trial.ref}.` : revertToast(proposal),
          }),
        onError: (error) => toast.show({ message: `Not done: ${error.message}` }),
      },
    );
  return (
    <li
      data-testid={`steer-trial-${trial.ref}`}
      className="flex min-w-0 flex-col gap-1 border-t border-border/60 py-2.5 first:border-0 first:pt-0"
    >
      <span className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
        <FlaskConicalIcon aria-hidden className="size-3" />
        <Link
          to={steerPageHref(steer.factoryId, "versions", { peek: trial.ref })}
          componentId="factory.steer.trial_ref"
          className="font-mono hover:text-foreground hover:underline"
        >
          {trial.ref}
        </Link>
        · {proposerName(trial)}
        {blocks.length > 0 && <> · {blocks.join(", ")}</>}
        {days && <> · {days}</>}
        {progress?.overdue && <> · past its window</>}
      </span>
      <span className="text-sm font-medium break-words">{trial.title}</span>
      {promise && <span className="text-sm text-foreground/80">Promise: {promise}</span>}
      {card.data?.scorecard ? (
        <ScorecardLine card={card.data.scorecard} testId={`steer-trial-guards-${trial.ref}`} />
      ) : card.isLoading ? (
        <span aria-busy="true" className="h-4 w-40 animate-pulse rounded bg-muted/70" />
      ) : null}
      {steer.owner && (
        <span className="mt-1 flex items-center gap-1.5">
          <Button
            size="xs"
            variant="outline"
            onClick={() => act("keep")}
            loading={decide.isPending && decide.variables?.action === "keep"}
            data-testid={`steer-trial-keep-${trial.ref}`}
            componentId="factory.steer.trial_keep"
          >
            Keep now
          </Button>
          <Button
            size="xs"
            variant="ghost"
            onClick={() => act("revert")}
            loading={decide.isPending && decide.variables?.action === "revert"}
            data-testid={`steer-trial-revert-${trial.ref}`}
            componentId="factory.steer.trial_revert"
          >
            Revert
          </Button>
        </span>
      )}
    </li>
  );
}

export function RunningTrials() {
  const steer = useSteer();
  const trials = steer.definition.on_trial ?? [];
  return (
    <section
      data-testid="steer-running-trials"
      aria-labelledby="steer-running-trials-h"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex items-center gap-2">
        <h2 id="steer-running-trials-h" className="flex-1 text-ui font-semibold">
          Running trials
        </h2>
        {trials.length > 0 && (
          <span className="rounded-full bg-muted px-2 text-xs tabular-nums">{trials.length}</span>
        )}
      </div>
      {trials.length === 0 ? (
        <p className="text-sm text-muted-foreground">No trial running.</p>
      ) : (
        <ul className="flex min-w-0 flex-col">
          {trials.map((t) => (
            <TrialRow key={t.id} trial={t} />
          ))}
        </ul>
      )}
    </section>
  );
}
