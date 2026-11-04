/**
 * Home's initiative cards, above the project rows: one compact card per
 * outcome several projects build. The title links to its page; a state chip
 * (green while it moves, red when an acceptance step fails, grey at rest);
 * a bar of merged projects; then three facts: whether the walking skeleton
 * is on the base env (with a link to try it there), how the acceptance went,
 * and what it waits on.
 */

import {
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleIcon,
  ExternalLinkIcon,
  XCircleIcon,
} from "lucide-react";
import type { InitiativeLine } from "@/lib/factoryInitiativesApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import {
  type AcceptanceMark,
  type CardModel,
  type TryIt,
  cardModel,
  mergedText,
} from "./initiativeModel";

export function StateChip({ label, meaning }: CardModel["state"]) {
  const tokens = MEANING[meaning];
  return (
    <span
      data-testid="initiative-state"
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        tokens.surface,
        tokens.border,
        tokens.text,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full" style={{ background: tokens.hue }} />
      {label}
    </span>
  );
}

/** Merged projects out of all, one segment each (a 12+ project initiative shows a fill). */
export function MergedBar({ merged, total }: { merged: number; total: number }) {
  const label = mergedText(merged, total);
  if (total <= 0) return null;
  if (total > 12) {
    return (
      <div role="img" aria-label={label} className="h-[5px] w-full rounded-full bg-status-gray/25">
        <div
          className="h-full rounded-full bg-status-green"
          style={{ width: `${Math.round((merged / total) * 100)}%` }}
        />
      </div>
    );
  }
  return (
    <div role="img" aria-label={label} className="flex w-full gap-[3px]">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-[5px] flex-1 rounded-full",
            i < merged ? "bg-status-green" : "bg-status-gray/25",
          )}
        />
      ))}
    </div>
  );
}

export function MarkIcon({ mark, className }: { mark: AcceptanceMark; className?: string }) {
  const size = cn("size-3.5 shrink-0", className);
  if (mark === "pass") {
    return <CheckCircle2Icon aria-hidden className={cn(size, MEANING.working.text)} />;
  }
  if (mark === "fail") return <XCircleIcon aria-hidden className={cn(size, MEANING.broken.text)} />;
  if (mark === "skipped") {
    return <CircleDashedIcon aria-hidden className={cn(size, "text-muted-foreground")} />;
  }
  return <CircleIcon aria-hidden className={cn(size, "text-muted-foreground/60")} />;
}

/** `Try it on the base env ↗`, with the flag to turn on as its tooltip and hint. */
export function TryItLink({
  tryIt,
  className,
  testId,
  withHint = false,
}: {
  tryIt: TryIt;
  className?: string;
  testId?: string;
  withHint?: boolean;
}) {
  return (
    <span className={cn("inline-flex min-w-0 flex-wrap items-center gap-x-1.5", className)}>
      <a
        href={tryIt.url}
        target="_blank"
        rel="noreferrer"
        data-testid={testId}
        title={tryIt.hint ?? undefined}
        className={cn(
          "inline-flex items-center gap-0.5 font-medium text-foreground underline decoration-dotted underline-offset-2",
          FOCUS_RING,
        )}
      >
        Try it on the base env
        <ExternalLinkIcon aria-hidden className="size-3" />
      </a>
      {withHint && tryIt.hint && <span className="text-muted-foreground">· {tryIt.hint}</span>}
    </span>
  );
}

export function InitiativeCard({ line, className }: { line: InitiativeLine; className?: string }) {
  const card = cardModel(line);
  return (
    <article
      data-testid={`initiative-card-${card.ref}`}
      data-state={line.state}
      className={cn(
        "relative flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card px-4 py-3",
        className,
      )}
    >
      {line.state === "failing" && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] rounded-l-xl"
          style={{ background: MEANING.broken.hue }}
        />
      )}
      <header className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-xs text-muted-foreground">{card.ref}</span>
        <Link
          to={card.href}
          componentId="factory.home.initiative"
          className={cn(
            "min-w-0 grow text-sm font-semibold text-foreground underline-offset-2 hover:underline",
            FOCUS_RING,
          )}
        >
          {card.title}
        </Link>
        <StateChip {...card.state} />
      </header>
      <div className="flex min-w-0 items-center gap-3">
        <MergedBar merged={card.merged} total={card.total} />
        <span className="shrink-0 text-xs font-medium text-foreground tabular-nums">
          {mergedText(card.merged, card.total)}
        </span>
      </div>
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs text-muted-foreground">
        {card.skeleton && (
          <li className="flex min-w-0 items-center gap-1.5" data-testid="initiative-skeleton">
            <MarkIcon mark={card.skeleton.ok ? "pass" : "pending"} />
            <span className={cn(card.skeleton.ok && "text-foreground")}>{card.skeleton.text}</span>
            {card.tryIt && (
              <TryItLink tryIt={card.tryIt} className="shrink-0" testId="initiative-try" />
            )}
          </li>
        )}
        <li className="flex min-w-0 items-center gap-1.5" data-testid="initiative-acceptance">
          <MarkIcon mark={card.acceptance.mark} />
          <span
            className={cn(
              card.acceptance.mark === "fail" && cn("font-medium", MEANING.broken.text),
              card.acceptance.mark === "pass" && "text-foreground",
            )}
          >
            {card.acceptance.text}
          </span>
        </li>
        {card.waiting && (
          <li className="min-w-0 truncate pl-5" title={card.waiting}>
            {card.waiting}
          </li>
        )}
        {card.review && (
          <li className="min-w-0 truncate pl-5" title={card.review} data-testid="initiative-review">
            {card.review}
          </li>
        )}
      </ul>
    </article>
  );
}

/** Home's section of initiative cards (nothing when there are none). */
export function InitiativeCards({
  lines,
  className,
}: {
  lines: readonly InitiativeLine[];
  className?: string;
}) {
  if (!lines.length) return null;
  return (
    <section
      aria-labelledby="home-initiatives-h"
      data-testid="home-initiatives"
      className={cn("flex min-w-0 flex-col gap-2", className)}
    >
      <h2 id="home-initiatives-h" className="text-base font-semibold">
        Initiatives <span className="font-medium text-muted-foreground">{lines.length}</span>
      </h2>
      <div className="grid min-w-0 gap-2 md:grid-cols-2">
        {lines.map((line) => (
          <InitiativeCard key={line.ref} line={line} />
        ))}
      </div>
    </section>
  );
}
