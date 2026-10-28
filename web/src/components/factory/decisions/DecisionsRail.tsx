/**
 * The decisions rail: what the agents and the supervisor asked the human,
 * blocking first, then oldest. Each card answers in one click and shows its
 * delivery receipt; cards answered here stay until the page reloads, older
 * answers fold into "Answered (n)". The project page also folds withdrawn
 * and expired ones (`showClosed`).
 */

import { Fragment, type ReactNode, useState } from "react";
import { BanIcon, ChevronRightIcon, CircleCheckIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { DecisionView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText, decisionAnchorId } from "../board/boardModel";
import { useFactoryServerNowS } from "../factoryClock";
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
  /** Also fold withdrawn and expired decisions (the project page lists all of them). */
  showClosed?: boolean;
  /** Leave out the "Decisions · n open" line (a tab or section already says it). */
  hideHeading?: boolean;
  /** This project's open decisions lead (the dock on its page or its PR's). */
  pinProject?: string | null;
  /**
   * A card drawn in place of the decision card (the dock draws a proposal's
   * as its ProposalCard), or null for the decision card; `onAnswered` keeps
   * it in the rail once decided.
   */
  renderCard?: (
    decision: DecisionView,
    onAnswered: (decision: DecisionView) => void,
  ) => ReactNode | null;
}

function matches(decision: DecisionView, id: string | null): boolean {
  return id != null && (decision.id === id || decision.ref === id);
}

/**
 * Open decisions in rail order: `pinProject`'s first, then blocking, then the
 * longest waiting.
 */
export function orderOpenDecisions(
  decisions: DecisionView[],
  pinProject: string | null = null,
): DecisionView[] {
  const pinned = (d: DecisionView) => Number(pinProject != null && d.project_slug === pinProject);
  return decisions
    .filter((d) => d.status === "open")
    .sort(
      (a, b) =>
        pinned(b) - pinned(a) ||
        Number(b.blocking) - Number(a.blocking) ||
        a.created_at - b.created_at,
    );
}

/** A muted "Label (n)" fold under the cards. */
function Fold({
  testId,
  label,
  defaultOpen = false,
  children,
}: {
  testId: string;
  label: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen}>
      <CollapsibleTrigger
        data-testid={`${testId}-toggle`}
        className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <ChevronRightIcon
          aria-hidden
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
        />
        {label}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul data-testid={testId} className="mt-1 flex flex-col gap-2">
          {children}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** Withdrawn and expired decisions: ref, what became of it and when, the question. */
function ClosedDecisions({
  decisions,
  highlightId,
  clockOffsetS,
}: {
  decisions: DecisionView[];
  highlightId: string | null;
  clockOffsetS: number;
}) {
  const nowS = useFactoryServerNowS(clockOffsetS);
  return (
    <Fold
      testId="decisions-closed"
      label={`Withdrawn / expired (${decisions.length})`}
      defaultOpen={decisions.some((d) => matches(d, highlightId))}
    >
      {decisions.map((d) => (
        <li
          key={d.id}
          id={decisionAnchorId(d.ref)}
          data-testid={`decision-closed-${d.ref}`}
          data-status={d.status}
          data-highlighted={matches(d, highlightId) ? "true" : undefined}
          className={cn(
            "flex flex-col gap-0.5 border-l-2 border-border pl-2.5 text-sm",
            matches(d, highlightId) && "border-primary",
          )}
        >
          <p className="flex min-w-0 items-center gap-1 text-muted-foreground">
            <BanIcon aria-hidden className="size-3.5 shrink-0" />
            <span className="font-mono">{d.ref}</span>
            <span>
              {d.status} · {ageText(nowS - d.updated_at)}
            </span>
          </p>
          <p className="line-clamp-2" title={d.question}>
            {d.question}
          </p>
        </li>
      ))}
    </Fold>
  );
}

export function DecisionsRail({
  decisions,
  highlightId,
  clockOffsetS = 0,
  showClosed = false,
  hideHeading = false,
  pinProject = null,
  renderCard,
}: DecisionsRailProps) {
  // Answered from this rail: their cards stay (with the receipt) instead of folding away.
  const [mine, setMine] = useState<Record<string, DecisionView>>({});
  const open = orderOpenDecisions(decisions, pinProject);
  const kept = Object.values(mine).map(
    (local) => decisions.find((d) => d.id === local.id && d.status !== "open") ?? local,
  );
  const cards = [...open, ...kept.filter((d) => !open.some((o) => o.id === d.id))];
  const answered = decisions
    .filter((d) => d.status === "answered" && !(d.id in mine))
    .sort((a, b) => (b.answered_at ?? 0) - (a.answered_at ?? 0));
  const closed = showClosed
    ? decisions
        .filter((d) => (d.status === "withdrawn" || d.status === "expired") && !(d.id in mine))
        .sort((a, b) => b.updated_at - a.updated_at)
    : [];
  const remember = (stored: DecisionView) => setMine((prev) => ({ ...prev, [stored.id]: stored }));

  return (
    <section data-testid="decisions-rail" aria-label="Decisions" className="flex flex-col gap-2">
      <h2 className={cn("text-sm font-medium text-muted-foreground", hideHeading && "sr-only")}>
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
      {cards.map((decision) => {
        const custom = renderCard?.(decision, remember);
        if (custom) return <Fragment key={decision.id}>{custom}</Fragment>;
        return (
          <DecisionCard
            key={decision.id}
            decision={decision}
            highlighted={matches(decision, highlightId)}
            onAnswered={remember}
            clockOffsetS={clockOffsetS}
          />
        );
      })}
      {answered.length > 0 && (
        <Fold testId="decisions-answered" label={`Answered (${answered.length})`}>
          {answered.map((d) => (
            <li
              key={d.id}
              id={decisionAnchorId(d.ref)}
              className="flex flex-col gap-0.5 border-l-2 border-border pl-2.5 text-sm"
            >
              <p className="min-w-0 text-muted-foreground">
                <span className="font-mono">{d.ref}</span>{" "}
                <span className="text-foreground">“{answerLabel(d)}”</span>
                {d.answered_by && ` · ${d.answered_by.split("@")[0]}`}
              </p>
              <p className="line-clamp-1 text-muted-foreground" title={d.question}>
                {d.question}
              </p>
              <DecisionReceipt decision={d} />
            </li>
          ))}
        </Fold>
      )}
      {closed.length > 0 && (
        <ClosedDecisions decisions={closed} highlightId={highlightId} clockOffsetS={clockOffsetS} />
      )}
    </section>
  );
}
