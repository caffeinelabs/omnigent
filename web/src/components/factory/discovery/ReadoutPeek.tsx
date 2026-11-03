/**
 * A readout in the side panel: the predicted band and the actual dot, the
 * metric and how it was measured, the guardrails, the verdict by its fixed
 * rule, and the decision. A person keeps or drops the bet; going against the
 * recommendation needs a named reviewer and a reason, both kept on the
 * readout.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { decideReadout } from "@/lib/factoryDiscoveryApi";
import { cn } from "@/lib/utils";
import { signedText } from "../cards/betModel";
import { Peek } from "../ui/Peek";
import { RangeBar } from "../ui/RangeBar";
import { useFactoryToast } from "../ui/Toast";
import { MEANING, READOUT_MARK, READOUT_WORD } from "../ui/statusTokens";
import type { ReadoutView } from "./discoveryModel";
import { useRefreshDiscovery } from "./discoveryQueries";

const VERDICT_RULE: Record<string, string> = {
  won: "moved the promised way by at least the low end of the predicted range, with guardrails held",
  flat: "moved less than the low end of the predicted range",
  lost: "moved the wrong way, or a guardrail broke",
  inconclusive: "too little data, or a data check failed",
};

/** The decision the recommendation points to (`roll out` → keep). */
export function recommendedDecision(recommendation: string | null): "keep" | "drop" | null {
  const r = (recommendation ?? "").toLowerCase();
  if (r.includes("keep") || r.includes("roll out")) return "keep";
  if (r.includes("roll back") || r.includes("drop")) return "drop";
  return null;
}

export function ReadoutPeek({
  factoryId,
  readout,
  readoutId,
  canAct,
  onOpenOpportunity,
  onClose,
}: {
  factoryId: string;
  readout: ReadoutView | null;
  /** The id in the address; null keeps the panel closed. */
  readoutId: string | null;
  canAct: boolean;
  onOpenOpportunity: (ref: string) => void;
  onClose: () => void;
}) {
  const toast = useFactoryToast();
  const refresh = useRefreshDiscovery(factoryId);
  const [choice, setChoice] = useState<"keep" | "drop" | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const recommended = recommendedDecision(readout?.recommendation ?? null);
  const against = choice != null && recommended != null && choice !== recommended;
  const send = async () => {
    if (!readout || !choice) return;
    try {
      await decideReadout(factoryId, readout.id, {
        decision: choice,
        ...(against ? { reviewer: reviewer.trim(), reason: reason.trim() } : {}),
        ...(!against && reason.trim() ? { reason: reason.trim() } : {}),
      });
      refresh();
      setChoice(null);
      toast.show({
        message: `${choice === "keep" ? "Kept" : "Dropped"} ${readout.proposal ?? readout.id}.`,
      });
    } catch (error) {
      toast.show({
        message: `Could not decide: ${error instanceof Error ? error.message : String(error)}`,
      });
    }
  };
  return (
    <Peek
      open={readoutId != null}
      onOpenChange={(open) => !open && onClose()}
      title={readout?.title ?? readoutId ?? ""}
      headerExtra={
        readout?.verdict ? (
          <span
            className={cn(
              "shrink-0 rounded-md px-1.5 text-xs font-medium",
              readout.verdict === "won"
                ? cn(MEANING.working.surface, MEANING.working.text)
                : readout.verdict === "lost"
                  ? cn(MEANING.broken.surface, MEANING.broken.text)
                  : "bg-muted",
            )}
          >
            {READOUT_MARK[readout.verdict]} {READOUT_WORD[readout.verdict]}
            {readout.day != null ? ` · day ${readout.day}` : ""}
          </span>
        ) : undefined
      }
      footer={
        readout?.opportunity ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenOpportunity(readout.opportunity!)}
          >
            Open the opportunity
          </Button>
        ) : undefined
      }
    >
      {!readout ? (
        <p role="alert" className="text-sm">
          {readoutId} is not a readout of this factory.
        </p>
      ) : (
        <div data-testid={`readout-peek-${readout.id}`} className="flex flex-col gap-4 text-sm">
          {readout.low != null && readout.high != null && (
            <RangeBar
              low={readout.low}
              high={readout.high}
              actual={readout.actual}
              unit={readout.unit}
              verdict={readout.verdict}
              format={signedText}
              legend
            />
          )}
          <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1">
            {readout.metric && (
              <>
                <dt className="text-muted-foreground">Metric</dt>
                <dd>{readout.metric}</dd>
              </>
            )}
            {readout.method && (
              <>
                <dt className="text-muted-foreground">Method</dt>
                <dd>{readout.method.replace(/_/g, " ")}</dd>
              </>
            )}
            {readout.n != null && (
              <>
                <dt className="text-muted-foreground">Rests on</dt>
                <dd>n = {readout.n}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Guardrails</dt>
            <dd>
              {readout.guardrails.length === 0
                ? "none set"
                : readout.guardrails
                    .map((g) => `${g.name}: ${g.held ? "held" : "broken"}`)
                    .join(" · ")}
            </dd>
            {readout.verdict && (
              <>
                <dt className="text-muted-foreground">Verdict</dt>
                <dd>
                  {READOUT_WORD[readout.verdict]}: {VERDICT_RULE[readout.verdict]}
                </dd>
              </>
            )}
            {readout.recommendation && (
              <>
                <dt className="text-muted-foreground">Recommends</dt>
                <dd>{readout.recommendation}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Decision</dt>
            <dd>
              {readout.decision
                ? `${readout.decision.decision}${readout.decision.by ? ` by ${readout.decision.by}` : ""}${
                    readout.decision.against ? " (against the recommendation)" : ""
                  }${readout.decision.reason ? `: ${readout.decision.reason}` : ""}`
                : "not yet"}
            </dd>
            {readout.nextDay != null && (
              <>
                <dt className="text-muted-foreground">Next readout</dt>
                <dd>day {readout.nextDay}</dd>
              </>
            )}
          </dl>
          {!readout.decision && canAct && (
            <section
              data-testid="readout-decide"
              className="flex flex-col gap-2 rounded-lg border border-border p-3"
            >
              <h3 className="font-semibold">Keep or drop it?</h3>
              <div className="flex gap-1.5">
                {(["keep", "drop"] as const).map((d) => (
                  <Button
                    key={d}
                    type="button"
                    size="sm"
                    variant={choice === d ? "default" : "outline"}
                    aria-pressed={choice === d}
                    onClick={() => setChoice(d)}
                  >
                    {d === "keep" ? "Keep" : "Drop"}
                    {recommended === d ? " (recommended)" : ""}
                  </Button>
                ))}
              </div>
              {against && (
                <p className="text-muted-foreground">
                  This goes against the recommendation: name a reviewer and say why. Both stay on
                  the readout.
                </p>
              )}
              {against && (
                <Input
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                  placeholder="Reviewer"
                  aria-label="Reviewer"
                />
              )}
              {choice && (
                <Input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder={against ? "Why (required)" : "Why (optional)"}
                  aria-label="Reason"
                />
              )}
              {choice && (
                <Button
                  type="button"
                  size="sm"
                  className="w-fit"
                  disabled={against && (!reviewer.trim() || !reason.trim())}
                  onClick={() => void send()}
                >
                  {choice === "keep" ? "Keep it" : "Drop it"}
                </Button>
              )}
            </section>
          )}
        </div>
      )}
    </Peek>
  );
}
