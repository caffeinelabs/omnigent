// "Why it is not moving", numbered, computed by the server from live waits and
// holds; it reads "Moving now" once the cause clears. The header opens it
// under the status line; the node's chat can post the same card.

import { cn } from "@/lib/utils";
import { DecisionButtons } from "./DecisionButtons";
import { useNodeActions } from "./NodeContext";
import { AMBER } from "./nodeTokens";
import type { StatusLine, VerbAvail } from "./nodeTypes";
import { RichText } from "./RichText";

export function WhyCard({
  line,
  startNow,
  inChat = false,
}: {
  line: StatusLine;
  /** The node's Start now verb, offered as "Start it now instead". */
  startNow?: VerbAvail | null;
  /** In a chat thread the card carries its own answer (the header's line already does). */
  inChat?: boolean;
}) {
  const { onVerb } = useNodeActions();
  if (!line.why?.length) return null;
  const moving = line.kind === "moving";
  const answer = inChat && !moving && line.decision ? line.decision : null;
  const start = !moving && startNow?.enabled ? startNow : null;
  return (
    <section
      data-testid="v3-why"
      aria-label={line.why_title ?? "Why"}
      className={cn(
        "flex flex-col gap-1.5 rounded-[10px] border bg-muted/40 px-3.5 py-2.5 text-[13px]",
        moving ? "border-border" : "border-status-yellow/55",
      )}
    >
      <div
        className={cn(
          "flex flex-wrap items-baseline gap-2 text-[11px] tracking-[0.07em] uppercase",
          moving ? "text-status-blue-fg" : AMBER.text,
        )}
      >
        {line.why_title ?? (moving ? "Moving now" : "Why")}
        <span className="tracking-normal normal-case text-foreground-tertiary">
          · computed now from waits and holds
        </span>
      </div>
      <ol className="flex list-decimal flex-col gap-0.5 pl-5 text-muted-foreground marker:text-xs marker:text-foreground-tertiary">
        {line.why.map((item) => (
          <li
            key={item.html}
            className={cn(
              item.tone === "you" && cn("font-semibold", AMBER.text),
              item.tone === "ok" && "font-semibold text-status-blue-fg",
            )}
          >
            <RichText html={item.html} />
          </li>
        ))}
      </ol>
      {line.note && !moving && (
        <p className="text-[12.5px] text-foreground-tertiary">{line.note}</p>
      )}
      {(answer || start) && (
        <div className="flex flex-wrap gap-1.5">
          {answer && <DecisionButtons decision={answer} variant="tile" />}
          {start && (
            <button
              type="button"
              onClick={() => onVerb?.(start)}
              className="rounded-[7px] border border-border-strong bg-card-solid px-2 text-xs leading-[1.5] font-semibold hover:border-status-blue hover:text-status-blue-fg"
            >
              Start it now instead
            </button>
          )}
        </div>
      )}
    </section>
  );
}
