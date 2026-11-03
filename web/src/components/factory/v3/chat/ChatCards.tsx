// The node chat's own cards: an alarm the server closed (with its cause and
// the real ask), an offer ("It can start now", which opens a preview and
// turns into "Started" once the node moves), and an ask's card with every
// answer (always a click). The why card is `WhyCard` with `inChat`.

import { useState } from "react";
import { cn } from "@/lib/utils";
import { DecisionButtons } from "../DecisionButtons";
import { AMBER } from "../nodeTokens";
import type { DecisionRef } from "../nodeTypes";

const CARD = "flex min-w-0 flex-col gap-1 rounded-[10px] border px-3.5 py-2.5 text-[13px]";
const HEAD = "text-[11px] tracking-[0.07em] text-foreground-tertiary uppercase";
const BTN =
  "inline-flex items-center rounded-[7px] border px-2 text-xs leading-[1.6] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-status-blue";

export interface ClosedAlarm {
  /** The alarm's id ("D-48"). */
  id: string;
  /** The alarm as it was raised. */
  was: string;
  /** Why the server closed it. */
  cause: string;
  /** The ask that really holds the work. */
  realAsk?: string | null;
}

/** "Closed by the server · D-48": what it said, why it closed, the real ask. */
export function ClosedAlarmCard({ alarm }: { alarm: ClosedAlarm }) {
  return (
    <section
      data-testid="v3-closed-alarm"
      data-alarm={alarm.id}
      aria-label={`Closed by the server: ${alarm.id}`}
      className={cn(CARD, "border-border bg-muted/30")}
    >
      <span className={HEAD}>Closed by the server · {alarm.id}</span>
      <s className="text-foreground-tertiary">“{alarm.was}”</s>
      <span className="text-muted-foreground">
        {alarm.cause}
        {alarm.realAsk && (
          <>
            {" "}
            <b className="font-semibold text-foreground">The real ask is {alarm.realAsk}.</b>
          </>
        )}
      </span>
    </section>
  );
}

/** An offer on a node: preview it, or keep waiting; once the node moves it says so. */
export function OfferCard({
  who,
  title,
  text,
  action,
  started,
  onPreview,
}: {
  /** Who offers ("Atlas"). */
  who: string;
  title: string;
  text?: string | null;
  /** The preview it opens ("Preview start now"); absent when this viewer may not use the verb. */
  action?: string | null;
  /** The node moves now (the offer was taken). */
  started?: string | null;
  onPreview?: () => void;
}) {
  const [kept, setKept] = useState(false);
  if (started) {
    return (
      <section data-testid="v3-offer" data-state="started" className={cn(CARD, "border-border")}>
        <span className={HEAD}>{who} offered · started</span>
        <span className="text-muted-foreground">{started}</span>
      </section>
    );
  }
  return (
    <section
      data-testid="v3-offer"
      data-state={kept ? "kept" : "open"}
      aria-label={`${who} offers: ${title}`}
      className={cn(CARD, "border-status-blue/45 bg-status-blue/5")}
    >
      <span className={cn(HEAD, "text-status-blue-fg")}>{who} offers</span>
      <b className="font-semibold">{title}</b>
      {text && <span className="text-muted-foreground">{text}</span>}
      {kept ? (
        <span className="text-xs text-foreground-tertiary">You kept it waiting.</span>
      ) : (
        <span className="mt-0.5 flex flex-wrap gap-1.5">
          {action && onPreview && (
            <button
              type="button"
              data-testid="v3-offer-preview"
              onClick={onPreview}
              className={cn(
                BTN,
                "border-foreground bg-foreground text-background hover:opacity-90",
              )}
            >
              {action}
            </button>
          )}
          <button
            type="button"
            onClick={() => setKept(true)}
            className={cn(BTN, "border-border-strong bg-card-solid hover:border-status-blue")}
          >
            Keep waiting
          </button>
        </span>
      )}
    </section>
  );
}

/** An ask in the thread of the node it is about: who asks, how long, and every answer (a click). */
export function AskCard({ decision }: { decision: DecisionRef }) {
  const age = [decision.age, decision.expires ? `expires in ${decision.expires}` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <section
      data-testid="v3-ask-card"
      data-decision={decision.id}
      aria-label={`${decision.id}: ${decision.title ?? decision.verb}`}
      className={cn(CARD, "border-status-yellow/55", AMBER.surface)}
    >
      <span className="flex flex-wrap items-baseline gap-x-2 text-xs">
        <b className={cn("font-semibold", AMBER.text)}>
          {decision.id} · {decision.asker} asks
        </b>
        {age && <span className="text-foreground-tertiary">{age}</span>}
      </span>
      <b className="font-semibold">{decision.title ?? decision.verb}</b>
      {decision.text && <span className="text-muted-foreground">{decision.text}</span>}
      <span className="mt-0.5">
        <DecisionButtons decision={decision} variant="card" />
      </span>
    </section>
  );
}
