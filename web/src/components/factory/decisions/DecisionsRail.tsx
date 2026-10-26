/**
 * The decisions rail: what the agents asked the human, blocking ones first.
 *
 * Placeholder until the decisions package lands: read-only cards (question,
 * context, options with the recommended one starred) and a link to answer in
 * the asker's chat. Its replacement keeps these props.
 */

import {
  ChevronRightIcon,
  CircleCheckIcon,
  MessageSquareIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { DecisionView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { decisionAnchorId } from "../board/boardModel";
import { useFactoryNow } from "../factoryClock";
import { formatElapsed } from "../factoryFormat";

export interface DecisionsRailProps {
  /** The factory (root session) id answers post to. */
  factoryId: string;
  /** Open decisions plus recently answered ones, as the snapshot lists them. */
  decisions: DecisionView[];
  /** A decision id or ref (`D-3`) to scroll to and highlight (`?decision=`). */
  highlightId: string | null;
}

function matches(decision: DecisionView, id: string | null): boolean {
  return id != null && (decision.id === id || decision.ref === id);
}

function DecisionCard({
  decision,
  highlighted,
  nowS,
}: {
  decision: DecisionView;
  highlighted: boolean;
  nowS: number;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);
  const waited = formatElapsed(nowS - decision.created_at);
  const asker =
    decision.asked_by_title ?? (decision.source === "agent" ? "An agent" : "The supervisor");
  return (
    <article
      ref={ref}
      id={decisionAnchorId(decision.ref)}
      data-testid={`decision-card-${decision.ref}`}
      data-blocking={decision.blocking ? "true" : "false"}
      data-highlighted={highlighted ? "true" : undefined}
      className={cn(
        "flex flex-col gap-1.5 rounded-lg border bg-card p-3 text-sm",
        decision.blocking ? "border-warning/40" : "border-border",
        highlighted &&
          "animate-[user-msg-flash_800ms_ease-out] ring-2 ring-ring/60 motion-reduce:animate-none",
      )}
    >
      <p
        className={cn(
          "flex flex-wrap items-center gap-x-1.5 font-medium",
          decision.blocking ? "text-warning" : "text-muted-foreground",
        )}
      >
        {decision.blocking && <TriangleAlertIcon aria-hidden className="size-3.5" />}
        <span>{decision.blocking ? "Blocking" : "Question"}</span>
        {decision.project_slug && <span className="font-normal">· {decision.project_slug}</span>}
        <span className="font-mono font-normal">· {decision.ref}</span>
        {waited && <span className="ml-auto font-normal tabular-nums">{waited}</span>}
      </p>
      <p className="text-muted-foreground">{asker} asks</p>
      <p className="text-ui text-foreground">{decision.question}</p>
      {decision.context && (
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            Context
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{decision.context}</p>
          </CollapsibleContent>
        </Collapsible>
      )}
      {decision.options.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {decision.options.map((option, i) => (
            <li
              key={option.id}
              title={option.description ?? undefined}
              className={cn(
                "inline-flex h-6 items-center gap-1 rounded-md border px-1.5",
                option.recommended
                  ? "border-foreground/30 text-foreground"
                  : "border-border text-muted-foreground",
              )}
            >
              <span className="font-mono text-muted-foreground">{i + 1}</span>
              {option.label}
              {option.recommended && <span className="text-warning">★</span>}
            </li>
          ))}
        </ul>
      )}
      {decision.asked_by_session_id && (
        <Link
          to={`/c/${decision.asked_by_session_id}`}
          componentId="factory.decision.open_asker"
          className="inline-flex items-center gap-1 self-start text-muted-foreground hover:text-foreground"
        >
          <MessageSquareIcon aria-hidden className="size-3.5" />
          Answer in chat
        </Link>
      )}
    </article>
  );
}

export function DecisionsRail({ decisions, highlightId }: DecisionsRailProps) {
  const nowS = Math.floor(useFactoryNow().getTime() / 1000);
  const open = decisions
    .filter((d) => d.status === "open")
    .sort((a, b) => Number(b.blocking) - Number(a.blocking) || a.created_at - b.created_at);
  const answered = decisions.filter((d) => d.status === "answered");
  return (
    <section data-testid="decisions-rail" aria-label="Decisions" className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted-foreground">
        Decisions · <span className="tabular-nums">{open.length}</span> open
      </h2>
      {open.length === 0 && (
        <p
          data-testid="decisions-empty"
          className="flex items-start gap-1.5 rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground"
        >
          <CircleCheckIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-success" />
          Nothing needs you. Agents ask here instead of in chat.
        </p>
      )}
      {open.map((decision) => (
        <DecisionCard
          key={decision.id}
          decision={decision}
          highlighted={matches(decision, highlightId)}
          nowS={nowS}
        />
      ))}
      {answered.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            Answered ({answered.length})
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-1 flex flex-col gap-1 text-sm">
              {answered.map((d) => (
                <li key={d.id} className="text-muted-foreground">
                  <span className="font-mono">{d.ref}</span>{" "}
                  <span className="text-foreground">
                    “
                    {d.answer_text ??
                      d.options.find((o) => o.id === d.answer_option_id)?.label ??
                      "answered"}
                    ”
                  </span>
                  {d.answered_by && ` · ${d.answered_by}`}
                  {d.delivered_at != null &&
                    ` · delivered${d.delivered_to_title ? ` to ${d.delivered_to_title}` : ""} ✓`}
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}
