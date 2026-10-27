/**
 * The decisions rail: what the agents and the supervisor asked the human,
 * blocking first, then oldest. Each card answers in one click and shows its
 * delivery receipt; cards answered here stay until the page reloads, older
 * answers fold into "Answered (n)".
 */

import { useState } from "react";
import { ChevronRightIcon, CircleCheckIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { DecisionView } from "@/lib/factoryApi";
import { decisionAnchorId } from "../board/boardModel";
import { DecisionCard, DecisionReceipt, answerLabel } from "./DecisionCard";

export interface DecisionsRailProps {
  /** The factory (root session) id answers post to. */
  factoryId: string;
  /** Open decisions plus recently answered ones, as the snapshot lists them. */
  decisions: DecisionView[];
  /** A decision id or ref (`D-3`) to scroll to and highlight (`?decision=`). */
  highlightId: string | null;
  /** Seconds the server clock runs ahead of the browser's (wait times age on it). */
  clockOffsetS?: number;
}

function matches(decision: DecisionView, id: string | null): boolean {
  return id != null && (decision.id === id || decision.ref === id);
}

/** Open decisions in rail order: blocking first, then the longest waiting. */
export function orderOpenDecisions(decisions: DecisionView[]): DecisionView[] {
  return decisions
    .filter((d) => d.status === "open")
    .sort((a, b) => Number(b.blocking) - Number(a.blocking) || a.created_at - b.created_at);
}

export function DecisionsRail({ decisions, highlightId, clockOffsetS = 0 }: DecisionsRailProps) {
  // Answered from this rail: their cards stay (with the receipt) instead of folding away.
  const [mine, setMine] = useState<Record<string, DecisionView>>({});
  const open = orderOpenDecisions(decisions);
  const kept = Object.values(mine).map(
    (local) => decisions.find((d) => d.id === local.id && d.status !== "open") ?? local,
  );
  const cards = [...open, ...kept.filter((d) => !open.some((o) => o.id === d.id))];
  const answered = decisions
    .filter((d) => d.status === "answered" && !(d.id in mine))
    .sort((a, b) => (b.answered_at ?? 0) - (a.answered_at ?? 0));
  const remember = (stored: DecisionView) => setMine((prev) => ({ ...prev, [stored.id]: stored }));

  return (
    <section data-testid="decisions-rail" aria-label="Decisions" className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted-foreground">
        Decisions · <span className="tabular-nums">{open.length}</span> open
      </h2>
      {cards.length === 0 && (
        <p
          data-testid="decisions-empty"
          className="flex items-start gap-1.5 rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground"
        >
          <CircleCheckIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-success" />
          Nothing needs you. Agents ask here instead of in chat.
        </p>
      )}
      {cards.map((decision) => (
        <DecisionCard
          key={decision.id}
          decision={decision}
          highlighted={matches(decision, highlightId)}
          onAnswered={remember}
          clockOffsetS={clockOffsetS}
        />
      ))}
      {answered.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger
            data-testid="decisions-answered-toggle"
            className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            Answered ({answered.length})
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul data-testid="decisions-answered" className="mt-1 flex flex-col gap-2">
              {answered.map((d) => (
                <li
                  key={d.id}
                  id={decisionAnchorId(d.ref)}
                  className="flex flex-col gap-0.5 border-l-2 border-border pl-2.5 text-sm"
                >
                  <p className="min-w-0 text-muted-foreground">
                    <span className="font-mono">{d.ref}</span>{" "}
                    <span className="text-foreground">“{answerLabel(d)}”</span>
                    {d.answered_by && ` · ${d.answered_by}`}
                  </p>
                  <p className="line-clamp-1 text-muted-foreground" title={d.question}>
                    {d.question}
                  </p>
                  <DecisionReceipt decision={d} />
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}
