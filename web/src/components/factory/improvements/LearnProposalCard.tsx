/**
 * One Tier-1 change (`C-n`) on the Improvements page: what it changes, who
 * proposed and co-signed it, its class, the evidence and the metric it
 * should move, and the levers its state offers. On trial: progress, the live
 * scorecard, Keep now and Revert. Pending: Approve trial, Edit, Reject.
 * Code (Tier 2 off): the reason, Dismiss and "Copy as issue". Reject and
 * Dismiss ask for the reason the proposer reads, as every proposal card does.
 * Owners pull every lever; everyone else sees them disabled with who can.
 */

import { useState } from "react";
import {
  CheckIcon,
  ChevronRightIcon,
  ClipboardCopyIcon,
  FlaskConicalIcon,
  MessagesSquareIcon,
  PencilIcon,
  RotateCcwIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { showToast } from "@/components/ui/toast";
import { copyText } from "@/lib/clipboard";
import type { FactorySnapshot, ProposalView, Tier2StatusView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  STATE_LABEL,
  STATE_TONE,
  actorName,
  proposalState,
  proposerLine,
} from "../cards/proposalModel";
import { RejectProposalForm } from "../cards/RejectProposalForm";
import { controlErrorMessage } from "../controls/controlModel";
import { TONE_CHIP, formatElapsed } from "../factoryFormat";
import { improvementPageHref, roomPageHref } from "../factoryRoutes";
import { useAnswerProposal, useDecideTrial, useProposalScorecard } from "../insights/learnQueries";
import { LEVER_REASON, ReasonGated, useViewerCan } from "../shell/viewerCan";
import {
  changeLines,
  effectText,
  evidenceHref,
  issueText,
  tier2Plain,
  trialProgress,
} from "./improvementsModel";
import { ProposeChangeDialog } from "./ProposeChangeDialog";
import { ScorecardLine, ScorecardTable } from "./Scorecard";

function ChangeList({ proposal }: { proposal: ProposalView }) {
  const lines = changeLines(proposal);
  if (lines.length === 0) return null;
  return (
    <ul data-testid={`proposal-${proposal.ref}-changes`} className="flex min-w-0 flex-col gap-0.5">
      {lines.map((line) => (
        <li key={line.path} className="flex min-w-0 items-baseline gap-2 font-mono text-xs">
          <span
            className={cn(
              "shrink-0",
              line.sign === "+"
                ? "text-success"
                : line.sign === "-"
                  ? "text-destructive"
                  : "text-warning",
            )}
          >
            {line.sign}
          </span>
          <span className="shrink-0 text-muted-foreground">{line.path}</span>
          <span className="min-w-0 break-words">
            {line.before != null && line.after != null ? (
              <>
                <span className="text-muted-foreground line-through decoration-muted-foreground/50">
                  {line.before}
                </span>{" "}
                → {line.after}
              </>
            ) : (
              (line.after ?? line.before)
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function EvidenceChips({ factoryId, ids }: { factoryId: string; ids: readonly unknown[] }) {
  if (ids.length === 0) return <span className="text-muted-foreground">none</span>;
  return (
    <>
      {ids.map((raw) => {
        const id = String(raw);
        const href = evidenceHref(factoryId, id);
        return href ? (
          <Link
            key={id}
            to={href}
            componentId="factory.improvements.evidence"
            className="font-mono text-xs hover:underline"
          >
            {id}
          </Link>
        ) : (
          <span key={id} className="font-mono text-xs">
            {id}
          </span>
        );
      })}
    </>
  );
}

function TrialBar({ proposal, nowS }: { proposal: ProposalView; nowS: number }) {
  const progress = trialProgress(proposal, nowS);
  if (!progress) return null;
  return (
    <span
      data-testid={`proposal-${proposal.ref}-trial`}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground tabular-nums"
      title={
        progress.overdue ? "Past its window: it reverts unattended at twice the window" : undefined
      }
    >
      <span aria-hidden className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <span
          className={cn("block h-full", progress.overdue ? "bg-warning" : "bg-session-active")}
          style={{ width: `${progress.pct}%` }}
        />
      </span>
      {progress.elapsedH}h/{progress.totalH}h
    </span>
  );
}

export function LearnProposalCard({
  factoryId,
  proposal,
  snapshot,
  nowS,
  tier2,
  factoryName,
  expanded = false,
}: {
  factoryId: string;
  proposal: ProposalView;
  snapshot: FactorySnapshot | null;
  nowS: number;
  tier2?: Tier2StatusView | null;
  factoryName: string;
  /** Show the scorecard table open (the proposal page). */
  expanded?: boolean;
}) {
  const { can } = useViewerCan();
  const ownerReason = can.apply_tuning ? null : LEVER_REASON.apply_tuning;
  const state = proposalState(proposal);
  const isTrial = proposal.status === "trial";
  const scored =
    ["trial", "kept", "reverted"].includes(proposal.status) && proposal.kind !== "code";
  const scorecard = useProposalScorecard(factoryId, proposal.ref, scored);
  const card = scorecard.data?.scorecard ?? null;
  const decide = useDecideTrial(factoryId);
  const answer = useAnswerProposal(factoryId);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const code = proposal.kind === "code";
  // A code change runs through a decision only while Tier 2 is on.
  const pending = proposal.status === "pending" && (!code || Boolean(proposal.decision_id));
  // Recorded while Tier 2 is off: owners can still dismiss it.
  const recorded = code && proposal.status === "pending" && !proposal.decision_id;
  const revertable = !code && ["trial", "applied", "kept"].includes(proposal.status);
  const effect = effectText(proposal);
  const busy = decide.isPending || answer.isPending;
  const fail = (error: unknown) => setFailure(controlErrorMessage(error));

  const run = (verb: "keep" | "revert") =>
    decide.mutate(
      { ref: proposal.ref, action: verb },
      {
        onSuccess: () =>
          showToast(
            verb === "keep"
              ? `${proposal.ref} kept`
              : `${proposal.ref} reverted · parent version back in force`,
          ),
        onError: fail,
      },
    );
  const respond = (verb: "apply" | "reject", note?: string) => {
    setFailure(null);
    answer.mutate(
      { ref: proposal.ref, verb, note },
      {
        onSuccess: () => {
          setRejecting(false);
          showToast(
            verb === "reject"
              ? `${proposal.ref} ${recorded ? "dismissed" : "rejected"}${note && proposal.author_kind === "agent" ? " · the proposer gets your note" : ""}`
              : code
                ? `${proposal.ref} approved · the Smith lane takes it`
                : `${proposal.ref} approved · on trial`,
          );
        },
        onError: fail,
      },
    );
  };
  const rejectNote =
    proposal.status === "rejected" && typeof proposal.result?.note === "string"
      ? proposal.result.note
      : null;
  const rejectedBy = proposal.decided_by ? ` by ${actorName(proposal.decided_by, snapshot)}` : "";

  return (
    <article
      data-testid={`proposal-${proposal.ref}`}
      data-status={proposal.status}
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-3 text-sm"
    >
      <header className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Link
          to={improvementPageHref(factoryId, proposal.ref)}
          componentId="factory.improvements.proposal"
          className="font-mono font-medium hover:underline"
        >
          {proposal.ref}
        </Link>
        <span className="text-muted-foreground">{proposal.kind}</span>
        {proposal.class && (
          <span
            title={
              proposal.class === "A"
                ? "Cheaper or safer: a peer lead co-signs"
                : proposal.class === "B"
                  ? "Owners decide"
                  : "A person's edit"
            }
            className="rounded-md bg-muted px-1.5 text-xs leading-5 text-muted-foreground"
          >
            {proposal.class === "human" ? "human" : `class ${proposal.class}`}
          </span>
        )}
        {/* On a phone the title takes its own line under the chips, unclipped. */}
        <span
          data-testid={`proposal-${proposal.ref}-title`}
          title={proposal.title}
          className="min-w-0 flex-1 font-medium max-sm:order-last max-sm:basis-full max-sm:break-words sm:truncate"
        >
          {proposal.title}
        </span>
        <span className="ml-auto inline-flex shrink-0 items-center gap-2">
          {isTrial && <TrialBar proposal={proposal} nowS={nowS} />}
          <span
            data-testid={`proposal-${proposal.ref}-state`}
            className={cn("rounded-md px-1.5 py-0.5 text-xs", TONE_CHIP[STATE_TONE[state]])}
          >
            {isTrial && <FlaskConicalIcon aria-hidden className="mr-1 inline size-3" />}
            {STATE_LABEL[state]}
          </span>
        </span>
      </header>
      <p className="text-xs text-muted-foreground">
        {proposerLine(proposal, snapshot)} · {formatElapsed(nowS - proposal.created_at)} ago
        {proposal.def_version_from != null && proposal.def_version_to != null && (
          <>
            {" "}
            · v{proposal.def_version_from} → v{proposal.def_version_to}
          </>
        )}
        {proposal.status === "awaiting_cosign" && " · waiting for the peer lead to co-sign"}
      </p>
      <ChangeList proposal={proposal} />
      {proposal.rationale && (
        <p className="text-sm break-words text-foreground/90">{proposal.rationale}</p>
      )}
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
        <span>evidence:</span>
        <EvidenceChips factoryId={factoryId} ids={proposal.evidence} />
        {effect && <span>· target: {effect}</span>}
      </p>
      {code && (
        <p
          data-testid={`proposal-${proposal.ref}-tier2`}
          className="rounded-lg bg-muted px-2.5 py-1.5 text-xs text-muted-foreground"
        >
          {tier2?.enabled ? (
            "The Factory Smith lane is on: an approved code change becomes a Smith project and a PR to omnigent."
          ) : (
            <span title={tier2?.reason ?? undefined}>
              Smith lane off
              {tier2Plain(tier2?.reason) ? ` (${tier2Plain(tier2?.reason)})` : ""}: recorded with
              its evidence, never run.
            </span>
          )}
        </p>
      )}
      {scored && card && (
        <Collapsible defaultOpen={expanded}>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <ScorecardLine card={card} testId={`proposal-${proposal.ref}-scorecard`} />
            <CollapsibleTrigger className="group ml-auto inline-flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              <ChevronRightIcon
                aria-hidden
                className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
              />
              Scorecard
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="pt-2">
            <ScorecardTable card={card} testId={`proposal-${proposal.ref}-scorecard-table`} />
          </CollapsibleContent>
        </Collapsible>
      )}
      {scored && scorecard.isLoading && (
        <p className="text-xs text-muted-foreground">Scoring against the baseline…</p>
      )}
      {rejectNote && (
        <p
          data-testid={`proposal-${proposal.ref}-outcome`}
          className="min-w-0 text-sm break-words text-muted-foreground"
        >
          {`${code && !proposal.decision_id ? "Dismissed" : "Rejected"}${rejectedBy}: “${rejectNote}”`}
        </p>
      )}
      {rejecting && (pending || recorded) ? (
        <RejectProposalForm
          proposalRef={proposal.ref}
          verb={recorded ? "Dismiss" : "Reject"}
          busy={answer.isPending}
          onConfirm={(note) => respond("reject", note)}
          onCancel={() => setRejecting(false)}
        />
      ) : (
        <footer className="flex min-w-0 flex-wrap items-center gap-2">
          {isTrial && (
            <ReasonGated reason={ownerReason} testId={`proposal-${proposal.ref}-keep`}>
              <Button
                size="sm"
                onClick={() => run("keep")}
                disabled={busy}
                data-testid={`proposal-${proposal.ref}-keep`}
                componentId="factory.improvements.keep"
              >
                <CheckIcon />
                Keep now
              </Button>
            </ReasonGated>
          )}
          {revertable && (
            <ReasonGated reason={ownerReason} testId={`proposal-${proposal.ref}-revert`}>
              <Button
                size="sm"
                variant="outline"
                onClick={() => run("revert")}
                disabled={busy}
                data-testid={`proposal-${proposal.ref}-revert`}
                componentId="factory.improvements.revert"
              >
                <RotateCcwIcon />
                Revert
              </Button>
            </ReasonGated>
          )}
          {pending && (
            <ReasonGated reason={ownerReason} testId={`proposal-${proposal.ref}-approve`}>
              <Button
                size="sm"
                onClick={() => respond("apply")}
                disabled={busy}
                data-testid={`proposal-${proposal.ref}-approve`}
                componentId="factory.improvements.approve"
              >
                {code ? <CheckIcon /> : <FlaskConicalIcon />}
                {code ? "Approve" : "Approve trial"}
              </Button>
            </ReasonGated>
          )}
          {pending && !code && (
            <ReasonGated reason={ownerReason} testId={`proposal-${proposal.ref}-edit`}>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(true)}
                disabled={busy}
                data-testid={`proposal-${proposal.ref}-edit`}
                componentId="factory.improvements.edit"
              >
                <PencilIcon />
                Edit
              </Button>
            </ReasonGated>
          )}
          {(pending || recorded) && (
            <ReasonGated reason={ownerReason} testId={`proposal-${proposal.ref}-reject`}>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setFailure(null);
                  setRejecting(true);
                }}
                disabled={busy}
                data-testid={`proposal-${proposal.ref}-reject`}
                componentId="factory.improvements.reject"
                className="text-muted-foreground"
              >
                <XIcon />
                {recorded ? "Dismiss" : "Reject"}
              </Button>
            </ReasonGated>
          )}
          {code && (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                copyText(issueText(proposal, factoryName, window.location.origin)).then(
                  () => showToast(`${proposal.ref} copied as an issue`),
                  () => showToast("Couldn’t copy"),
                )
              }
              data-testid={`proposal-${proposal.ref}-copy-issue`}
              componentId="factory.improvements.copy_issue"
            >
              <ClipboardCopyIcon />
              Copy as issue
            </Button>
          )}
          {/* The list links #improvements once, above the tabs; the change's own page links it here. */}
          {expanded && proposal.thread && (
            <Link
              to={roomPageHref(factoryId, proposal.thread)}
              componentId="factory.improvements.discuss"
              className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <MessagesSquareIcon aria-hidden className="size-3.5" />
              discuss in #{proposal.thread}
            </Link>
          )}
        </footer>
      )}
      {failure && (
        <p data-testid={`proposal-${proposal.ref}-error`} className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {editing && (
        <ProposeChangeDialog
          factoryId={factoryId}
          open={editing}
          onOpenChange={setEditing}
          editing={proposal}
        />
      )}
    </article>
  );
}
