/**
 * A readout in the side panel: the predicted band and the actual dot, the
 * metric and how it was measured, the guardrails, the verdict by its fixed
 * rule, and the decision. A person decides a written readout with the
 * server's options (keep, drop, iterate, extend); going against the
 * recommendation needs a named reviewer and a reason, both kept on the
 * readout. A readout measured by a person's verdict waits for "did it help?"
 * with a reason; a decision can be reopened (the server says until when).
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { decideReadout, reopenReadout } from "@/lib/factoryDiscoveryApi";
import { cn } from "@/lib/utils";
import { signedText } from "../cards/betModel";
import { Peek } from "../ui/Peek";
import { RangeBar } from "../ui/RangeBar";
import { useFactoryToast } from "../ui/Toast";
import { MEANING, READOUT_MARK, READOUT_WORD } from "../ui/statusTokens";
import type { ReadoutAnswer, ReadoutDecision, ReadoutOption, ReadoutView } from "./discoveryModel";
import { useRefreshDiscovery } from "./discoveryQueries";

/** The fixed rules, for a server that sends no why-line of its own. */
const VERDICT_RULE: Record<string, string> = {
  won: "moved the promised way by at least the smallest predicted change, with guardrails held",
  flat: "moved less than the smallest predicted change (a small move the wrong way is noise)",
  lost: "moved the wrong way by at least that much, or a guardrail broke",
  inconclusive: "too little data, or a data check failed",
};

/** The decision the recommendation points to (`roll out` → keep). */
export function recommendedDecision(recommendation: string | null): "keep" | "drop" | null {
  const r = (recommendation ?? "").toLowerCase().replace(/_/g, " ");
  if (r.includes("keep") || r.includes("roll out")) return "keep";
  if (r.includes("roll back") || r.includes("drop")) return "drop";
  return null;
}

const DECISION_WORD: Record<ReadoutDecision, string> = {
  keep: "Kept",
  drop: "Dropped",
  iterate: "Iterating on",
  extend: "Extended",
};

const ANSWERS: { answer: ReadoutAnswer; label: string }[] = [
  { answer: "helped", label: "It helped" },
  { answer: "no_change", label: "No change" },
  { answer: "hurt", label: "It hurt" },
];

/** What a person may decide: the server's options, else keep or drop by the recommendation. */
export function decisionChoices(readout: ReadoutView): ReadoutOption[] {
  if (readout.options.length) return readout.options;
  const recommended = recommendedDecision(readout.recommendation);
  return (["keep", "drop"] as const).map((d) => ({
    decision: d,
    label: d === "keep" ? "Keep it" : "Drop it",
    recommended: recommended === d,
    against: recommended != null && recommended !== d,
  }));
}

function guardrailWord(g: ReadoutView["guardrails"][number]): string {
  if (!g.measured) return "not measured";
  return g.held ? "held" : "broken";
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
  const [choice, setChoice] = useState<ReadoutDecision | null>(null);
  const [verdict, setVerdict] = useState<ReadoutAnswer | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const choices = readout ? decisionChoices(readout) : [];
  const against = choices.find((o) => o.decision === choice)?.against ?? false;
  // A readout from a server without statuses decides like a written one.
  const written = readout?.status ? readout.status === "written" : !readout?.decision;
  const awaiting = readout?.status === "awaiting_answer";
  const failed = (what: string, error: unknown) =>
    toast.show({
      message: `Could not ${what}: ${error instanceof Error ? error.message : String(error)}`,
    });
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
      toast.show({ message: `${DECISION_WORD[choice]} ${readout.proposal ?? readout.id}.` });
    } catch (error) {
      failed("decide", error);
    }
  };
  const answer = async () => {
    if (!readout || !verdict || !reason.trim()) return;
    try {
      await decideReadout(factoryId, readout.id, { answer: verdict, reason: reason.trim() });
      refresh();
      setVerdict(null);
      setReason("");
      toast.show({ message: `Answered for ${readout.proposal ?? readout.id}.` });
    } catch (error) {
      failed("answer", error);
    }
  };
  const reopen = async () => {
    if (!readout) return;
    try {
      await reopenReadout(factoryId, readout.id);
      refresh();
      toast.show({ message: `Reopened ${readout.proposal ?? readout.id}: decide it again.` });
    } catch (error) {
      failed("reopen", error);
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
                : readout.guardrails.map((g) => `${g.name}: ${guardrailWord(g)}`).join(" · ")}
            </dd>
            {readout.verdict && (
              <>
                <dt className="text-muted-foreground">Verdict</dt>
                <dd>
                  {READOUT_WORD[readout.verdict]}: {readout.why ?? VERDICT_RULE[readout.verdict]}
                </dd>
              </>
            )}
            {readout.recommendation && (
              <>
                <dt className="text-muted-foreground">Recommends</dt>
                <dd>{readout.recommendation.replace(/_/g, " ")}</dd>
              </>
            )}
            {readout.waiting && (
              <>
                <dt className="text-muted-foreground">Waits</dt>
                <dd data-testid="readout-waiting">{readout.waiting}</dd>
              </>
            )}
            {readout.answer?.answer && (
              <>
                <dt className="text-muted-foreground">Answer</dt>
                <dd>
                  {readout.answer.answer.replace(/_/g, " ")}
                  {readout.answer.by ? ` by ${readout.answer.by}` : ""}
                  {readout.answer.reason ? `: ${readout.answer.reason}` : ""}
                </dd>
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
          {awaiting && canAct && (
            <section
              data-testid="readout-answer"
              className="flex flex-col gap-2 rounded-lg border border-border p-3"
            >
              <h3 className="font-semibold">Did it help?</h3>
              <p className="text-muted-foreground">
                This bet is measured by a person&apos;s verdict: answer with a reason.
              </p>
              <div className="flex flex-wrap gap-1.5">
                {ANSWERS.map((a) => (
                  <Button
                    key={a.answer}
                    type="button"
                    size="sm"
                    variant={verdict === a.answer ? "default" : "outline"}
                    aria-pressed={verdict === a.answer}
                    onClick={() => setVerdict(a.answer)}
                  >
                    {a.label}
                  </Button>
                ))}
              </div>
              {verdict && (
                <Input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why (required)"
                  aria-label="Reason"
                />
              )}
              {verdict && (
                <Button
                  type="button"
                  size="sm"
                  className="w-fit"
                  disabled={!reason.trim()}
                  onClick={() => void answer()}
                >
                  Send the answer
                </Button>
              )}
            </section>
          )}
          {written && !readout.decision && canAct && (
            <section
              data-testid="readout-decide"
              className="flex flex-col gap-2 rounded-lg border border-border p-3"
            >
              <h3 className="font-semibold">What now?</h3>
              <div className="flex flex-wrap gap-1.5">
                {choices.map((o) => (
                  <Button
                    key={o.decision}
                    type="button"
                    size="sm"
                    variant={choice === o.decision ? "default" : "outline"}
                    aria-pressed={choice === o.decision}
                    onClick={() => setChoice(o.decision)}
                  >
                    {o.label}
                    {o.recommended ? " (recommended)" : ""}
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
                  {choices.find((o) => o.decision === choice)?.label ?? choice}
                </Button>
              )}
            </section>
          )}
          {readout.status === "decided" && canAct && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-fit"
              data-testid="readout-reopen"
              onClick={() => void reopen()}
            >
              Reopen the decision
            </Button>
          )}
        </div>
      )}
    </Peek>
  );
}
