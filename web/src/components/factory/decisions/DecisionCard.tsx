/**
 * One decision an agent (or the supervisor) asked the human, answered in
 * one click: an option button, or free text. The answer shows at once as
 * "Answered · delivering…" (rolled back if the POST fails), then as a
 * receipt once the server delivers it to the asker. A lost race reads
 * "Already answered by X". Keyboard: 1–5 pick an option on the focused
 * card, ⌘↵ sends the text.
 */

import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  BanIcon,
  CheckCheckIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  ExternalLinkIcon,
  Loader2Icon,
  MessageSquareIcon,
  SendIcon,
  StarIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import type { DecisionConflictBody, DecisionOptionView, DecisionView } from "@/lib/factoryApi";
import { FactoryApiError, answerDecision } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText, decisionAnchorId } from "../board/boardModel";
import { useFactoryServerNowS } from "../factoryClock";
import { formatElapsed } from "../factoryFormat";
import { FoldableMarkdown } from "../transcript/FoldableMarkdown";

/** An open decision waits this long before its wait time turns red. */
export const DECISION_RED_AFTER_S = 30 * 60;

/** Digits 1–5 pick an option on a focused card. */
const KEY_OPTIONS = 5;

type Phase =
  | { kind: "idle" }
  | { kind: "answering"; label: string }
  | { kind: "answered"; decision: DecisionView; label: string }
  | {
      kind: "conflict";
      /** What the decision became meanwhile: answered elsewhere, withdrawn or expired. */
      status: "answered" | "withdrawn" | "expired";
      by: string | null;
      at: number | null;
    }
  | { kind: "failed"; message: string; decision: DecisionView | null }
  | { kind: "error"; message: string };

export type DecisionCardStatus =
  "open" | "answering" | "answered" | "delivered" | "conflict" | "withdrawn" | "expired";

export interface DecisionCardProps {
  /** The freshest server view of the decision. */
  decision: DecisionView;
  /** Highlight it (`?decision=`): scroll into view and pulse. */
  highlighted?: boolean;
  /** The factory's name, shown when cards from several factories mix (Inbox). */
  factoryName?: string;
  /** Called with the server's answered view once an answer is stored. */
  onAnswered?: (decision: DecisionView) => void;
  /** Seconds the server clock runs ahead of the browser's (wait times age on it). */
  clockOffsetS?: number;
  className?: string;
}

/** `14:03:06`: a receipt's wall-clock time. */
export function receiptTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** The answer's words: the picked option's label and/or the free text. */
export function answerLabel(decision: DecisionView): string {
  const option = decision.options.find((o) => o.id === decision.answer_option_id);
  if (option && decision.answer_text) return `${option.label} · ${decision.answer_text}`;
  return option?.label ?? decision.answer_text ?? "answered";
}

function askerText(decision: DecisionView): string {
  if (decision.asked_by_title) return `${decision.asked_by_title} asks`;
  return decision.source === "agent" ? "An agent asks" : "The supervisor asks";
}

function kindLabel(decision: DecisionView): string {
  if (decision.source !== "agent") return "Needs human";
  return decision.blocking ? "Blocking" : "Question";
}

/** The receipt line of an answered decision (also used by the rail's answered list). */
export function DecisionReceipt({ decision }: { decision: DecisionView }) {
  const delivered = decision.delivered_at != null;
  // A delivery is pending while the answer's mail id waits for its receipt.
  const awaiting = !delivered && decision.delivered_mail_id != null;
  return (
    <p
      data-testid={`decision-receipt-${decision.ref}`}
      data-state={delivered ? "delivered" : awaiting ? "delivering" : "answered"}
      className={cn(
        "flex flex-wrap items-center gap-x-1 text-sm",
        delivered ? "text-success" : "text-muted-foreground",
      )}
    >
      {delivered ? (
        <>
          <CheckCheckIcon aria-hidden className="size-3.5" />
          Delivered to {decision.delivered_to_title ?? "the asker"}{" "}
          {/* The time and its tick wrap together, never a lone ✓. */}
          <span className="whitespace-nowrap">
            <span className="font-mono tabular-nums">{receiptTime(decision.delivered_at!)}</span> ✓
          </span>
        </>
      ) : awaiting ? (
        <>
          <Loader2Icon aria-hidden className="size-3.5 animate-spin motion-reduce:animate-none" />
          Answered · delivering…
        </>
      ) : (
        <>
          <CheckCheckIcon aria-hidden className="size-3.5" />
          Answered{decision.answered_by ? ` by ${decision.answered_by}` : ""} ✓
        </>
      )}
    </p>
  );
}

function OptionButton({
  decision,
  option,
  index,
  disabled,
  picked,
  onPick,
}: {
  decision: DecisionView;
  option: DecisionOptionView;
  index: number;
  disabled: boolean;
  picked: boolean;
  onPick: (option: DecisionOptionView) => void;
}) {
  const testId = `decision-option-${decision.ref}-${index}`;
  const body = (
    <>
      {index < KEY_OPTIONS && (
        <kbd className="font-mono text-[10px] text-muted-foreground">{index + 1}</kbd>
      )}
      <span className="min-w-0 truncate">{option.label}</span>
      {option.recommended && (
        <StarIcon
          role="img"
          aria-label="recommended"
          className="size-3 shrink-0 fill-current text-warning"
        />
      )}
      {option.href && <ExternalLinkIcon aria-hidden className="size-3 shrink-0" />}
    </>
  );
  const className = cn(
    "h-auto min-h-7 max-w-full justify-start py-1 font-normal whitespace-normal",
    option.recommended && "border-foreground/25",
    picked && "border-ring bg-muted",
  );
  if (option.href) {
    const external = /^https?:/.test(option.href);
    return (
      <Button
        asChild
        variant="outline"
        size="sm"
        className={className}
        title={option.description ?? undefined}
      >
        {external ? (
          <a href={option.href} target="_blank" rel="noreferrer" data-testid={testId}>
            {body}
          </a>
        ) : (
          <Link to={option.href} componentId="factory.decision.option_link" data-testid={testId}>
            {body}
          </Link>
        )}
      </Button>
    );
  }
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => onPick(option)}
      disabled={disabled}
      data-testid={testId}
      data-recommended={option.recommended ? "true" : undefined}
      title={option.description ?? undefined}
      componentId="factory.decision.option"
      className={className}
    >
      {body}
    </Button>
  );
}

export function DecisionCard({
  decision,
  highlighted = false,
  factoryName,
  onAnswered,
  clockOffsetS = 0,
  className,
}: DecisionCardProps) {
  const queryClient = useQueryClient();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const ref = useRef<HTMLElement>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [composing, setComposing] = useState(false);
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);

  const invalidate = () => {
    for (const key of factoryInvalidationKeys(decision.factory_id)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  // The server's answer wins once it arrives; until then, the one this card sent.
  const answered: DecisionView | null =
    decision.status === "answered"
      ? decision
      : phase.kind === "answered"
        ? phase.decision
        : phase.kind === "failed"
          ? phase.decision
          : null;
  const open = decision.status === "open" && answered == null && phase.kind !== "conflict";
  const busy = phase.kind === "answering";

  const submit = (answer: { option_id?: string; text?: string }, label: string) => {
    if (busy || !open) return;
    setPhase({ kind: "answering", label });
    answerDecision(decision.id, answer).then(
      ({ decision: stored }) => {
        setPhase({ kind: "answered", decision: stored, label });
        setText("");
        setComposing(false);
        invalidate();
        onAnswered?.(stored);
      },
      (error: unknown) => {
        if (error instanceof FactoryApiError && error.status === 409) {
          const body = (error.body ?? {}) as DecisionConflictBody;
          const now = body.status ?? body.decision?.status;
          setPhase({
            kind: "conflict",
            status: now === "withdrawn" || now === "expired" ? now : "answered",
            by: body.answered_by ?? body.decision?.answered_by ?? null,
            at: body.answered_at ?? body.decision?.answered_at ?? null,
          });
          invalidate();
          return;
        }
        if (error instanceof FactoryApiError && error.code === "action_failed") {
          const body = (error.body ?? {}) as DecisionConflictBody;
          setPhase({ kind: "failed", message: error.message, decision: body.decision ?? null });
          invalidate();
          return;
        }
        // Roll back: the options come back and the draft stays for a retry.
        setPicked(null);
        setPhase({
          kind: "error",
          message: error instanceof Error ? error.message : String(error),
        });
      },
    );
  };

  const pick = (option: DecisionOptionView) => {
    if (option.href) return;
    setPicked(option.id);
    const draft = text.trim();
    submit(
      draft && decision.allow_text
        ? { option_id: option.id, text: draft }
        : { option_id: option.id },
      option.label,
    );
  };

  const sendText = () => {
    const draft = text.trim();
    if (!draft) return;
    setPicked(null);
    submit({ text: draft }, draft);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!open) return;
    const inText = event.target instanceof HTMLTextAreaElement;
    if (inText) {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        sendText();
      }
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (!/^[1-5]$/.test(event.key)) return;
    const option = decision.options[Number(event.key) - 1];
    if (!option || option.href) return;
    event.preventDefault();
    pick(option);
  };

  const status: DecisionCardStatus =
    decision.status === "withdrawn" || decision.status === "expired"
      ? decision.status
      : phase.kind === "conflict"
        ? "conflict"
        : answered
          ? answered.delivered_at != null
            ? "delivered"
            : "answered"
          : busy
            ? "answering"
            : "open";

  const waited = nowS - decision.created_at;
  const late = open && waited > DECISION_RED_AFTER_S;
  const serverAsked = decision.source !== "agent";
  const tone = serverAsked
    ? "text-destructive"
    : decision.blocking
      ? "text-warning"
      : "text-muted-foreground";

  // Withdrawn or expired: one quiet line.
  if (status === "withdrawn" || status === "expired") {
    return (
      <article
        ref={ref}
        id={decisionAnchorId(decision.ref)}
        data-testid={`decision-card-${decision.ref}`}
        data-status={status}
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground",
          className,
        )}
      >
        <BanIcon aria-hidden className="size-3.5" />
        <span className="font-mono">{decision.ref}</span>
        <span className="min-w-0 truncate">{decision.question}</span>
        <span className="ml-auto shrink-0">{status}</span>
      </article>
    );
  }

  return (
    <article
      ref={ref}
      id={decisionAnchorId(decision.ref)}
      tabIndex={0}
      onKeyDown={onKeyDown}
      data-testid={`decision-card-${decision.ref}`}
      data-status={status}
      data-blocking={decision.blocking ? "true" : "false"}
      data-highlighted={highlighted ? "true" : undefined}
      aria-label={`${decision.ref}: ${decision.question}`}
      className={cn(
        "relative flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        open && decision.blocking
          ? serverAsked
            ? "border-destructive/35"
            : "border-warning/40"
          : "border-border",
        // An outline, not a ring: dark mode's glass card rule owns box-shadow.
        highlighted &&
          "animate-[user-msg-flash_800ms_ease-out] outline-2 outline-offset-1 outline-ring outline-solid motion-reduce:animate-none",
        className,
      )}
    >
      {/* The card edge carries the tone too: dark mode's glass card rule owns the border. */}
      {open && decision.blocking && (
        <span
          aria-hidden
          className={cn(
            "absolute inset-y-3 left-0 w-[3px] rounded-r-full",
            serverAsked ? "bg-destructive" : "bg-warning",
          )}
        />
      )}
      <p className={cn("flex min-w-0 flex-wrap items-center gap-x-1.5 font-medium", tone)}>
        {serverAsked ? (
          <CircleAlertIcon aria-hidden className="size-3.5 shrink-0" />
        ) : decision.blocking ? (
          <TriangleAlertIcon aria-hidden className="size-3.5 shrink-0" />
        ) : (
          <MessageSquareIcon aria-hidden className="size-3.5 shrink-0" />
        )}
        <span>{kindLabel(decision)}</span>
        {/* Parts lead with their own space so the line reads as one phrase. */}
        {factoryName && (
          <span className="font-normal text-muted-foreground">{` · ${factoryName}`}</span>
        )}
        {decision.project_slug && (
          <span className="font-normal">{` · ${decision.project_slug}`}</span>
        )}
        <span className="font-normal">
          {" · "}
          <span className="font-mono">{decision.ref}</span>
        </span>
        {open ? (
          <span
            data-testid={`decision-wait-${decision.ref}`}
            data-late={late ? "true" : undefined}
            title="Waiting on you"
            className={cn(
              "ml-auto font-mono font-normal tabular-nums",
              late ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {formatElapsed(waited)}
          </span>
        ) : (
          <span className="ml-auto font-normal text-success">Answered</span>
        )}
      </p>
      <p className="text-muted-foreground">{askerText(decision)}</p>
      <p className={cn("text-ui break-words", open ? "text-foreground" : "text-muted-foreground")}>
        {decision.question}
      </p>
      {decision.context && (
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex cursor-pointer items-center gap-1 self-start rounded-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            Context
          </CollapsibleTrigger>
          <CollapsibleContent>
            <FoldableMarkdown
              text={decision.context}
              className="mt-1 text-sm text-muted-foreground"
            />
          </CollapsibleContent>
        </Collapsible>
      )}

      {open && (
        <>
          {busy ? (
            <p
              data-testid={`decision-receipt-${decision.ref}`}
              data-state="answering"
              className="flex items-center gap-1.5 text-muted-foreground"
            >
              <Loader2Icon
                aria-hidden
                className="size-3.5 animate-spin motion-reduce:animate-none"
              />
              Answered “{phase.label}” · delivering…
            </p>
          ) : (
            decision.options.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {decision.options.map((option, i) => (
                  <OptionButton
                    key={option.id}
                    decision={decision}
                    option={option}
                    index={i}
                    disabled={busy}
                    picked={picked === option.id}
                    onPick={pick}
                  />
                ))}
              </div>
            )
          )}
          {decision.allow_text &&
            !busy &&
            (composing ? (
              <div className="flex flex-col gap-1.5">
                <Textarea
                  data-testid={`decision-text-${decision.ref}`}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Your answer…"
                  aria-label={`Answer ${decision.ref}`}
                  autoFocus
                  componentId="factory.decision.text"
                  className="min-h-14 bg-background"
                />
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={sendText}
                    disabled={!text.trim()}
                    data-testid={`decision-send-${decision.ref}`}
                    componentId="factory.decision.send"
                  >
                    <SendIcon />
                    Send
                  </Button>
                  <span className="text-muted-foreground">⌘↵</span>
                </div>
              </div>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setComposing(true)}
                data-testid={`decision-answer-${decision.ref}`}
                componentId="factory.decision.answer"
                // Flush with the card's text; the ghost padding shows on hover only.
                className="-ml-2.5 self-start text-muted-foreground"
              >
                <MessageSquareIcon />
                Answer…
              </Button>
            ))}
          {phase.kind === "error" && (
            <p data-testid={`decision-error-${decision.ref}`} className="text-destructive">
              Couldn’t send: {phase.message}. Try again.
            </p>
          )}
        </>
      )}

      {phase.kind === "conflict" && (
        <p
          data-testid={`decision-conflict-${decision.ref}`}
          className="flex items-center gap-1.5 text-muted-foreground"
        >
          <CircleAlertIcon aria-hidden className="size-3.5" />
          {phase.status === "withdrawn"
            ? "Withdrawn by the asker meanwhile"
            : phase.status === "expired"
              ? "Expired before your answer arrived"
              : `Already answered${phase.by ? ` by ${phase.by}` : ""}${phase.at != null ? ` ${ageText(nowS - phase.at)}` : ""}`}
        </p>
      )}

      {answered && (
        <>
          <p className="text-foreground">
            <span className="text-muted-foreground">Answer: </span>“
            {answered.answer_option_id || answered.answer_text
              ? answerLabel(answered)
              : phase.kind === "answered"
                ? phase.label
                : "answered"}
            ”
            {answered.answered_by && (
              <span className="text-muted-foreground"> · {answered.answered_by}</span>
            )}
          </p>
          {phase.kind === "failed" ? (
            <p data-testid={`decision-failed-${decision.ref}`} className="text-destructive">
              {phase.message}
            </p>
          ) : (
            <DecisionReceipt decision={answered} />
          )}
        </>
      )}

      {decision.asked_by_session_id && open && (
        <Link
          to={`/c/${decision.asked_by_session_id}`}
          componentId="factory.decision.open_asker"
          className="inline-flex items-center gap-1 self-start text-muted-foreground hover:text-foreground"
        >
          <ExternalLinkIcon aria-hidden className="size-3" />
          Open {decision.asked_by_title ?? "the asker"}’s chat
        </Link>
      )}
    </article>
  );
}
