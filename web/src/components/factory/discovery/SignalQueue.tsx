/**
 * Discovery's Signals: the queue of signals no opportunity holds yet, with
 * Scout's label, and Attach (as evidence for an opportunity, or against it).
 * Signal text is untrusted user text, redacted before anyone reads it. A
 * signal that reads like instructions is shown struck through as a possible
 * injection, excluded from scoring, and offers no action at all.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { FAINT_TEXT } from "../ui/statusTokens";
import type { OpportunityRow, SignalView } from "./discoveryModel";
import { ExcludedNote } from "./ExcludedNote";

function day(at: number | null): string | null {
  if (at == null) return null;
  return new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function Attach({
  signal,
  targets,
  onAttach,
}: {
  signal: SignalView;
  targets: readonly OpportunityRow[];
  onAttach: (signal: string, opportunity: string, counter: boolean) => void;
}) {
  const [target, setTarget] = useState(targets[0]?.id ?? "");
  const [counter, setCounter] = useState(false);
  if (targets.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No open opportunity to attach it to yet. Scout groups signals in its weekly synthesis.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label className="flex min-w-0 items-center gap-1.5">
        <span className="text-muted-foreground">Attach to</span>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          data-testid={`signal-target-${signal.id}`}
          className="h-8 max-w-64 min-w-0 truncate rounded-md border border-border bg-background px-2 text-sm"
        >
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.id} · {t.title}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-muted-foreground">
        <input
          type="checkbox"
          checked={counter}
          onChange={(e) => setCounter(e.target.checked)}
          data-testid={`signal-counter-${signal.id}`}
        />
        as evidence against
      </label>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!target}
        onClick={() => onAttach(signal.id, target, counter)}
        data-testid={`signal-attach-${signal.id}`}
      >
        Attach
      </Button>
    </div>
  );
}

function SignalItem({
  signal,
  targets,
  canAct,
  onAttach,
}: {
  signal: SignalView;
  targets: readonly OpportunityRow[];
  canAct: boolean;
  onAttach: (signal: string, opportunity: string, counter: boolean) => void;
}) {
  const meta = [
    signal.id,
    signal.kind,
    signal.intensity,
    signal.segments.join(", ") || null,
    day(signal.observedAt),
  ].filter(Boolean);
  return (
    <li
      data-testid={`signal-${signal.id}`}
      data-injection={signal.injection || undefined}
      data-actionable={signal.actionable || undefined}
      className="flex min-w-0 gap-3 border-t border-border px-4 py-3 first:border-t-0"
    >
      <span className="h-fit shrink-0 rounded border border-border px-1 font-mono text-xs text-muted-foreground">
        {signal.source}
      </span>
      <div className="flex min-w-0 grow flex-col gap-1">
        {signal.text ? (
          <q
            className={cn(
              "text-sm break-words",
              signal.injection && cn("line-through", FAINT_TEXT),
            )}
          >
            {signal.text}
          </q>
        ) : (
          <p className={cn("text-sm", FAINT_TEXT)}>Text {signal.gone ?? "not kept"}.</p>
        )}
        <p className="flex flex-wrap gap-x-2 font-mono text-xs text-muted-foreground">
          {meta.map((m) => (
            <span key={m}>{m}</span>
          ))}
        </p>
        {signal.paraphrase && !signal.injection && (
          <p className="text-xs text-muted-foreground">Scout: {signal.paraphrase}</p>
        )}
        {signal.injection || !signal.actionable ? (
          <ExcludedNote signal={signal} />
        ) : (
          canAct && <Attach signal={signal} targets={targets} onAttach={onAttach} />
        )}
      </div>
    </li>
  );
}

export function SignalQueue({
  unsorted,
  flagged,
  waiting,
  targets,
  canAct,
  loading,
  onAttach,
}: {
  unsorted: readonly SignalView[] | null;
  flagged: readonly SignalView[] | null;
  /** The server's count of unsorted signals (the list shows at most 100). */
  waiting: number | null;
  /** Open opportunities a signal can be attached to. */
  targets: readonly OpportunityRow[];
  canAct: boolean;
  loading: boolean;
  onAttach: (signal: string, opportunity: string, counter: boolean) => void;
}) {
  const items = [
    ...(unsorted ?? []),
    ...(flagged ?? []).filter((f) => !unsorted?.some((u) => u.id === f.id)),
  ];
  return (
    <section
      aria-labelledby="signals-h"
      data-testid="discovery-signals"
      className="min-w-0 rounded-xl border border-border bg-card"
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-4 py-3">
        <h2 id="signals-h" className="grow text-base font-semibold">
          Unsorted signals
        </h2>
        <span className="text-sm text-muted-foreground">
          {waiting != null ? `${waiting} waiting · ` : ""}redacted before anyone reads them · the
          text is untrusted user text
        </span>
      </header>
      {loading && items.length === 0 ? (
        <div aria-busy="true" className="m-4 h-24 animate-pulse rounded-lg bg-muted" />
      ) : items.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          <b className="text-foreground">All sorted.</b> New signals appear here after each sync.
        </p>
      ) : (
        <ul className="m-0 list-none p-0">
          {items.map((s) => (
            <SignalItem
              key={s.id}
              signal={s}
              targets={targets}
              canAct={canAct}
              onAttach={onAttach}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
