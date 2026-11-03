/**
 * "Proposed by agents": the changes Scout, Sentinel or Atlas filed to how the
 * factory works, each with why, its evidence and when it expires. Stage it
 * puts the change in the staged column (with its preview and re-rank) and
 * replaces what was staged, with Undo; Apply then answers the proposal. Reject
 * tells the agent. Agents file at most two a day; a proposal expires after 72 h.
 */

import { BotIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ProposalView } from "@/lib/factoryApi";
import { formatElapsed } from "../factoryFormat";
import { steerPageHref } from "../factoryRoutes";
import { EvidenceChips } from "../improvements/LearnProposalCard";
import { useAnswerProposal } from "../insights/learnQueries";
import { Link } from "@/lib/routing";
import { useFactoryToast } from "../ui/Toast";
import { useSteer } from "./SteerContext";
import { agentProposals, expiresIn, proposerName, stageProposal, stagedCount } from "./steerModel";

export function AgentProposals({
  proposals,
  loading,
}: {
  proposals: readonly ProposalView[] | undefined;
  loading: boolean;
}) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const answer = useAnswerProposal(steer.factoryId);
  const rows = agentProposals(proposals ?? []);
  const stage = (proposal: ProposalView) => {
    const before = steer.staging;
    steer.setStaging(stageProposal(proposal, steer.effective));
    toast.show({
      message:
        stagedCount(before) > 0
          ? `Staged ${proposal.ref}; what was staged before is set aside.`
          : `Staged ${proposal.ref}. See its effect, then apply it.`,
      undo: () => steer.setStaging(before),
    });
  };
  const reject = (proposal: ProposalView) =>
    answer.mutate(
      { ref: proposal.ref, verb: "reject" },
      {
        onSuccess: () =>
          toast.show({ message: `Rejected ${proposal.ref}. ${proposerName(proposal)} is told.` }),
        onError: (error) => toast.show({ message: `Not rejected: ${error.message}` }),
      },
    );
  return (
    <section
      data-testid="steer-agent-proposals"
      aria-labelledby="steer-agent-proposals-h"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex items-center gap-2">
        <h2 id="steer-agent-proposals-h" className="flex-1 text-ui font-semibold">
          Proposed by agents
        </h2>
        {rows.length > 0 && (
          <span className="rounded-full bg-muted px-2 text-xs tabular-nums">{rows.length}</span>
        )}
      </div>
      {loading && !proposals ? (
        <div aria-busy="true" className="h-16 animate-pulse rounded-md bg-muted/70" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No proposals. Agents may each file two a day, with evidence; they expire after 72 h.
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col">
          {rows.map((p) => {
            const left = expiresIn(p, steer.nowS);
            const staged = steer.staging.proposal?.ref === p.ref;
            return (
              <li
                key={p.id}
                data-testid={`steer-proposal-${p.ref}`}
                className="flex min-w-0 flex-col gap-1 border-t border-border/60 py-2.5 first:border-0 first:pt-0"
              >
                <span className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                  <BotIcon aria-hidden className="size-3" />
                  <Link
                    to={steerPageHref(steer.factoryId, "versions", { peek: p.ref })}
                    componentId="factory.steer.proposal_ref"
                    className="font-mono hover:text-foreground hover:underline"
                  >
                    {p.ref}
                  </Link>
                  · {proposerName(p)} · expires in {formatElapsed(left) ?? "a moment"}
                </span>
                <span className="text-sm font-medium break-words">{p.title}</span>
                {p.rationale && (
                  <span className="text-sm text-foreground/80 break-words">{p.rationale}</span>
                )}
                {p.evidence.length > 0 && (
                  <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    Evidence:{" "}
                    <EvidenceChips factoryId={steer.factoryId} ids={p.evidence.slice(0, 6)} />
                    {p.evidence.length > 6 && <span>and {p.evidence.length - 6} more</span>}
                  </span>
                )}
                {steer.canEdit && (
                  <span className="mt-1 flex items-center gap-1.5">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => stage(p)}
                      disabled={staged}
                      data-testid={`steer-proposal-stage-${p.ref}`}
                      componentId="factory.steer.proposal_stage"
                    >
                      {staged ? "Staged" : "Stage it"}
                    </Button>
                    {steer.owner && (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => reject(p)}
                        loading={answer.isPending && answer.variables?.ref === p.ref}
                        data-testid={`steer-proposal-reject-${p.ref}`}
                        componentId="factory.steer.proposal_reject"
                      >
                        Reject
                      </Button>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
