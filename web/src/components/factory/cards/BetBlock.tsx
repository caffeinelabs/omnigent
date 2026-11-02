/**
 * A projects proposal's bet beside the other lead's case against it, so the
 * person who approves reads both at once. The bet: kind and origin, the
 * outcome, the hypothesis, the predicted range, metric, method and readout
 * days, guardrails, flag, kill rule and cost. Requested work from an
 * opportunity has no bet: one line says it is not measured.
 */

import type { FactorySnapshot, ProposalView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { RangeBar } from "../ui/RangeBar";
import {
  BET_KIND_LABEL,
  METHOD_LABEL,
  betOrigin,
  caseCopy,
  caseState,
  costText,
  expectedText,
  flagText,
  killText,
  readoutText,
  signedText,
  waitsForCosign,
} from "./betModel";

const EYEBROW = "text-xs font-medium tracking-wide text-muted-foreground uppercase";

function Row({ label, children }: { label: string; children: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

export interface BetBlockProps {
  proposal: ProposalView;
  /** Names the lead that wrote the case against. */
  snapshot?: FactorySnapshot | null;
  className?: string;
}

export function BetBlock({ proposal, snapshot = null, className }: BetBlockProps) {
  const bet = proposal.bet;
  if (!bet) {
    if (!proposal.opportunity) return null;
    return (
      <p
        data-testid={`proposal-why-${proposal.ref}`}
        className={cn("text-muted-foreground", className)}
      >
        From {proposal.opportunity} · requested, no bet: not measured
      </p>
    );
  }
  const origin = betOrigin(proposal);
  const cost = costText(bet.cost);
  const state = caseState(proposal);
  const copy = caseCopy(proposal, snapshot);
  return (
    <section
      data-testid={`proposal-bet-${proposal.ref}`}
      aria-label={`Bet and case against, ${proposal.ref}`}
      className={cn("@container flex min-w-0 flex-col gap-2", className)}
    >
      <div className="grid min-w-0 gap-2 @xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border p-2.5">
          <p className={EYEBROW}>
            {BET_KIND_LABEL[bet.kind]}
            {origin && <span className="normal-case tracking-normal"> · {origin}</span>}
          </p>
          <p className="font-medium break-words">{bet.outcome}</p>
          <p className="break-words text-muted-foreground">{bet.hypothesis}</p>
          <div className="flex min-w-0 flex-col gap-0.5">
            <RangeBar
              low={bet.expected.low}
              high={bet.expected.high}
              unit={bet.expected.unit}
              format={signedText}
            />
            <p className="text-xs text-muted-foreground">
              {expectedText(bet.expected)} · not measured yet
            </p>
          </div>
          <dl className="grid min-w-0 grid-cols-[5.75rem_minmax(0,1fr)] gap-x-2 gap-y-0.5">
            <Row label="Metric">{bet.metric}</Row>
            <Row label="Measured by">
              {`${METHOD_LABEL[bet.measurement.method]}, ${readoutText(bet.measurement.readout_days)}`}
            </Row>
            {bet.guardrails.length > 0 && (
              <Row label="Guardrails">{bet.guardrails.join(" · ")}</Row>
            )}
            <Row label="Flag">{flagText(bet.flag)}</Row>
            <Row label="Kill if">{killText(bet.kill)}</Row>
            {cost && <Row label="Cost">{cost}</Row>}
          </dl>
        </div>
        <div
          data-testid={`proposal-case-${proposal.ref}`}
          data-state={state}
          className={cn(
            "flex min-w-0 flex-col gap-1.5 rounded-md border p-2.5",
            state === "written" ? "border-border-strong" : "border-dashed border-border-strong",
          )}
        >
          <p className={EYEBROW}>{copy.heading}</p>
          {state === "written" ? (
            <ol className="flex list-decimal flex-col gap-1 pl-4">
              {(proposal.case_against ?? []).map((line) => (
                <li key={line} className="break-words">
                  {line}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-muted-foreground">{copy.empty}</p>
          )}
        </div>
      </div>
      {!waitsForCosign(proposal) && (
        <p className="text-xs text-muted-foreground">
          {proposal.status === "applied"
            ? "Frozen into the charter of each new project when it was applied."
            : "If you approve, the bet is frozen into the charter of each new project: the metric, the range and the readout days cannot move after you have seen data."}
        </p>
      )}
    </section>
  );
}
