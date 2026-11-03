/**
 * Home's Needs you: the server's one list (`GET …/asks`), one row per
 * subject, sorted by the cost of waiting, at most five rows and then "and N
 * more". Each row has a kind icon, one sentence, one line of context, its
 * facts (project, who asks, what waiting costs, age, deadline), a small id
 * link and its main button; a question is answered in place, with Undo.
 * Under it one calm line says what the factory handled itself. All | Mine
 * narrows it to what is assigned to the viewer by name.
 */

import {
  CheckIcon,
  CircleHelpIcon,
  FilePenLineIcon,
  FileTextIcon,
  FlaskConicalIcon,
  GaugeIcon,
  GitMergeIcon,
  type LucideIcon,
  PlugIcon,
  TargetIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Ask, AskKind, AskOption, AsksResponse } from "@/lib/factoryAsksApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { FactoryLoadError } from "../FactoryLoadError";
import { PlaceCount } from "../shell/FactoryTabs";
import { FAINT_TEXT, FOCUS_RING, MEANING } from "../ui/statusTokens";
import { type NeedsFilter, askMeta, needsView } from "./homeModel";
import type { DeferredAnswers } from "./useDeferredAnswers";

const KIND_ICON: Record<AskKind, LucideIcon> = {
  decision: CircleHelpIcon,
  merge: GitMergeIcon,
  bet: FlaskConicalIcon,
  source: PlugIcon,
  readout: GaugeIcon,
  broken: TriangleAlertIcon,
  profile: FileTextIcon,
  goal: TargetIcon,
  proposal: FilePenLineIcon,
};

/** Red only for what is broken; every other ask is amber (it needs you). */
function isRed(ask: Ask): boolean {
  return ask.kind === "broken" && (ask.critical || ask.decision_id != null);
}

function KindIcon({ ask }: { ask: Ask }) {
  const Icon = KIND_ICON[ask.kind] ?? CircleHelpIcon;
  const tone = isRed(ask) ? MEANING.broken : MEANING.needsYou;
  return (
    <span
      aria-hidden
      className={cn(
        "mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg",
        tone.surface,
        tone.text,
      )}
    >
      <Icon className="size-3.5" />
    </span>
  );
}

function OptionButtons({
  ask,
  options,
  onAnswer,
}: {
  ask: Ask;
  options: readonly AskOption[];
  onAnswer: (option: AskOption) => void;
}) {
  const oneClick = options.filter((o) => !o.href && !o.reasons);
  const links = options.filter((o) => o.href);
  const ordered = [...oneClick].sort((a, b) => Number(b.recommended) - Number(a.recommended));
  const more = options.some((o) => o.reasons) || ask.allow_text;
  return (
    <>
      {ordered.map((option) => (
        <Button
          key={option.id}
          type="button"
          size="sm"
          variant={option.recommended ? "default" : "outline"}
          data-testid={`need-${ask.id}-option-${option.id}`}
          componentId="factory.home.needs.answer"
          onClick={() => onAnswer(option)}
          className="max-sm:w-full"
        >
          {option.label}
          {option.recommended ? " (recommended)" : ""}
        </Button>
      ))}
      {links.map((option) => (
        <Button key={option.id} asChild size="sm" variant="outline" className="max-sm:w-full">
          <Link to={option.href!} componentId="factory.home.needs.option_link">
            {option.label}
          </Link>
        </Button>
      ))}
      {more && (
        <Button asChild size="sm" variant="ghost" className="max-sm:w-full">
          <Link
            to={ask.href}
            componentId="factory.home.needs.more"
            data-testid={`need-${ask.id}-more`}
          >
            {oneClick.length ? "Another answer…" : "Answer…"}
          </Link>
        </Button>
      )}
    </>
  );
}

function PrimaryButton({ ask, onPeek }: { ask: Ask; onPeek: (peek: string) => void }) {
  const action = ask.primary_action;
  if (action.kind === "peek" && action.peek) {
    return (
      <Button
        type="button"
        size="sm"
        data-testid={`need-${ask.id}-primary`}
        componentId="factory.home.needs.peek"
        onClick={() => onPeek(action.peek!)}
        className="max-sm:w-full"
      >
        {action.label}
      </Button>
    );
  }
  return (
    <Button asChild size="sm" className="max-sm:w-full">
      <Link
        to={action.href ?? ask.href}
        componentId="factory.home.needs.open"
        data-testid={`need-${ask.id}-primary`}
      >
        {action.label}
      </Link>
    </Button>
  );
}

export function NeedRow({
  ask,
  nowS,
  canAnswer,
  onAnswer,
  onPeek,
}: {
  ask: Ask;
  nowS: number;
  canAnswer: boolean;
  onAnswer: (ask: Ask, option: AskOption) => void;
  onPeek: (peek: string) => void;
}) {
  const inPlace = canAnswer && ask.decision_id != null && ask.options?.length ? ask.options : null;
  const meta = askMeta(ask, nowS);
  return (
    <li
      data-testid={`need-${ask.id}`}
      data-kind={ask.kind}
      className="flex gap-3 border-t border-border px-4 py-3 first:border-t-0"
    >
      <KindIcon ask={ask} />
      <div className="min-w-0 grow">
        <p className="text-sm font-semibold text-foreground">{ask.title}</p>
        {ask.context && <p className="mt-0.5 text-sm text-muted-foreground">{ask.context}</p>}
        {ask.items.length > 0 && (
          <ul aria-label="Pull requests" className="mt-1.5 flex flex-wrap gap-1.5">
            {ask.items.map((item) => (
              <li key={item.href}>
                <Link
                  to={item.href}
                  componentId="factory.home.needs.item"
                  title={`${item.ref}${item.project ? ` · ${item.project}` : ""}`}
                  className={cn(
                    "inline-block max-w-full truncate rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground hover:bg-muted/70",
                    FOCUS_RING,
                  )}
                >
                  {item.title}
                </Link>
              </li>
            ))}
            {ask.more_count > 0 && (
              <li className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                and {ask.more_count} more
              </li>
            )}
          </ul>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
          {meta.map((part, i) => (
            <span key={part} className="flex items-center gap-x-1.5">
              {i > 0 && <span aria-hidden>·</span>}
              {part}
            </span>
          ))}
          {ask.ref && (
            <span className="flex items-center gap-x-1.5">
              {meta.length > 0 && <span aria-hidden>·</span>}
              <Link
                to={ask.href}
                componentId="factory.home.needs.ref"
                className={cn(
                  "font-mono underline decoration-dotted underline-offset-2 hover:text-foreground",
                  FOCUS_RING,
                )}
              >
                {ask.ref}
              </Link>
            </span>
          )}
          {ask.folded.length > 0 && (
            <span className={cn("flex items-center gap-x-1.5", FAINT_TEXT)}>
              <span aria-hidden>·</span>
              also {ask.folded.join(", ")}
            </span>
          )}
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {inPlace ? (
            <OptionButtons ask={ask} options={inPlace} onAnswer={(o) => onAnswer(ask, o)} />
          ) : (
            <PrimaryButton ask={ask} onPeek={onPeek} />
          )}
        </div>
      </div>
    </li>
  );
}

function Skeleton() {
  return (
    <div aria-busy="true" aria-label="Loading what needs you" className="flex flex-col gap-3 p-4">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex gap-3">
          <div className="size-7 animate-pulse rounded-lg bg-muted" />
          <div className="flex grow flex-col gap-1.5">
            <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export interface NeedsYouProps {
  asks: AsksResponse | undefined;
  loading: boolean;
  error: Error | null;
  updatedAt?: number;
  onRetry: () => void;
  nowS: number;
  /** Projects agents work on, for the calm empty line. */
  moving: number;
  canAnswer: boolean;
  deferred: DeferredAnswers;
  onPeek: (peek: string) => void;
  className?: string;
}

export function NeedsYou({
  asks,
  loading,
  error,
  updatedAt,
  onRetry,
  nowS,
  moving,
  canAnswer,
  deferred,
  onPeek,
  className,
}: NeedsYouProps) {
  const [filter, setFilter] = useState<NeedsFilter>("all");
  const [expanded, setExpanded] = useState(false);
  const rows = useMemo(() => asks?.asks ?? [], [asks]);
  const { hidden, prune, answer } = deferred;
  useEffect(() => {
    if (asks) prune(new Set(rows.map((a) => a.id)));
  }, [asks, rows, prune]);
  const view = needsView(rows, filter, expanded, hidden);
  const all = needsView(rows, "all", true, hidden).total;
  const handled = asks?.handled ?? [];
  return (
    <section
      aria-labelledby="home-needs-h"
      data-testid="home-needs"
      className={cn("rounded-xl border border-border bg-card", className)}
    >
      <header className="flex items-center gap-2 px-4 pt-3 pb-2">
        <h2 id="home-needs-h" className="flex grow items-center gap-2 text-base font-semibold">
          Needs you
          <PlaceCount
            n={all}
            tone="needsYou"
            label={`${all} ${all === 1 ? "thing needs" : "things need"} you`}
            testId="home-needs-count"
          />
        </h2>
        <div role="group" aria-label="Whose asks" className="flex rounded-lg bg-muted p-0.5">
          {(["all", "mine"] as const).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              data-testid={`home-needs-filter-${f}`}
              onClick={() => setFilter(f)}
              className={cn(
                "cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium",
                filter === f ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                FOCUS_RING,
              )}
            >
              {f === "all" ? "All" : "Mine"}
            </button>
          ))}
        </div>
      </header>
      {error != null && !asks ? (
        <div className="px-4 pb-3">
          <FactoryLoadError
            error={error}
            updatedAt={updatedAt ?? 0}
            onRetry={onRetry}
            what="what needs you"
            testId="home-needs-error"
            className="mb-0"
          />
        </div>
      ) : loading && !asks ? (
        <Skeleton />
      ) : view.total === 0 ? (
        <div data-testid="home-needs-empty" className="flex gap-3 border-t border-border px-4 py-3">
          <span
            aria-hidden
            className={cn(
              "grid size-7 shrink-0 place-items-center rounded-lg",
              MEANING.working.surface,
              MEANING.working.text,
            )}
          >
            <CheckIcon className="size-3.5" />
          </span>
          <div>
            <p className="text-sm font-semibold">
              {filter === "mine" && all > 0
                ? "Nothing is assigned to you by name."
                : "Nothing needs you."}
            </p>
            <p className="text-sm text-muted-foreground">
              {filter === "mine" && all > 0
                ? `${all} ${all === 1 ? "ask waits" : "asks wait"} for your role.`
                : moving
                  ? `${moving} ${moving === 1 ? "project is" : "projects are"} moving.`
                  : "Nothing is in progress either."}
            </p>
          </div>
        </div>
      ) : (
        <ul className="m-0 list-none border-t border-border p-0" data-testid="home-needs-rows">
          {view.shown.map((ask) => (
            <NeedRow
              key={ask.id}
              ask={ask}
              nowS={nowS}
              canAnswer={canAnswer}
              onAnswer={answer}
              onPeek={onPeek}
            />
          ))}
        </ul>
      )}
      {(view.more > 0 || expanded) && view.total > 5 && (
        <div className="border-t border-border px-4 py-2">
          <button
            type="button"
            aria-expanded={expanded}
            data-testid="home-needs-more"
            onClick={() => setExpanded((open) => !open)}
            className={cn(
              "cursor-pointer rounded text-sm text-status-blue-fg underline-offset-2 hover:underline",
              FOCUS_RING,
            )}
          >
            {expanded ? "Show fewer" : `and ${view.more} more`}
          </button>
        </div>
      )}
      {handled.length > 0 && (
        <div
          data-testid="home-handled"
          className="flex gap-2 border-t border-border px-4 py-2.5 text-sm text-muted-foreground"
        >
          <CheckIcon aria-hidden className={cn("mt-0.5 size-3.5 shrink-0", MEANING.working.text)} />
          <p>
            <span className="font-medium text-foreground">Handled by the factory.</span>{" "}
            {handled.join(" ")}
          </p>
        </div>
      )}
    </section>
  );
}
