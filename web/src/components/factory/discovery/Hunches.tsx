/**
 * Hunches: each "I think … because …" from the factory's people (and the
 * leads' own ideas, counted as hunches), with what came of it; and the
 * composer. A hunch is stored next to the evidence with weight 0.1 and is
 * never mixed into user evidence; Scout looks for evidence and proposes the
 * cheapest test.
 */

import { LightbulbIcon } from "lucide-react";
import { type FormEvent, forwardRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { FAINT_TEXT, FOCUS_RING } from "../ui/statusTokens";
import { type OpportunityRow, type SignalView, hunchOutcome } from "./discoveryModel";

/** "I think … because …": a person's hunch, optionally on an opportunity. */
export const HunchComposer = forwardRef<
  HTMLInputElement,
  {
    id: string;
    onAdd: (text: string) => Promise<boolean>;
    className?: string;
  }
>(function HunchComposer({ id, onAdd, className }, ref) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const hunch = text.trim();
    if (!hunch || busy) return;
    setBusy(true);
    const ok = await onAdd(hunch);
    setBusy(false);
    if (ok) setText("");
  };
  return (
    <form
      onSubmit={(e) => void submit(e)}
      data-testid={`hunch-composer-${id}`}
      className={cn("flex flex-wrap gap-2", className)}
    >
      <label htmlFor={`hunch-${id}`} className="sr-only">
        Add a hunch
      </label>
      <Input
        ref={ref}
        id={`hunch-${id}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="I think … because …"
        autoComplete="off"
        maxLength={1000}
        className="h-8 min-w-0 flex-1 basis-56"
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy || !text.trim()}>
        <LightbulbIcon aria-hidden className="size-3.5" />
        Add hunch
      </Button>
    </form>
  );
});

export function Hunches({
  hunches,
  rows,
  loading,
  canAct,
  mayWithdraw,
  onOpen,
  onWithdraw,
  onAdd,
}: {
  hunches: readonly SignalView[] | null;
  rows: readonly OpportunityRow[];
  loading: boolean;
  canAct: boolean;
  /** Whether the viewer may withdraw this hunch (their own, or an owner). */
  mayWithdraw: (hunch: SignalView) => boolean;
  onOpen: (ref: string) => void;
  onWithdraw: (ref: string) => void;
  onAdd: (text: string) => Promise<boolean>;
}) {
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section
        aria-labelledby="hunches-h"
        data-testid="discovery-hunches"
        className="min-w-0 rounded-xl border border-border bg-card"
      >
        <h2 id="hunches-h" className="border-b border-border px-4 py-3 text-base font-semibold">
          Hunches and what came of them
        </h2>
        {loading && !hunches ? (
          <div aria-busy="true" className="m-4 h-24 animate-pulse rounded-lg bg-muted" />
        ) : !hunches || hunches.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No hunches yet. Add one: Scout looks for evidence and proposes the cheapest test.
          </p>
        ) : (
          <ul className="m-0 list-none p-0">
            {hunches.map((h) => (
              <li
                key={h.id}
                data-testid={`hunch-${h.id}`}
                className="flex min-w-0 gap-3 border-t border-border px-4 py-3 first:border-t-0"
              >
                <LightbulbIcon
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                />
                <div className="flex min-w-0 grow flex-col gap-1">
                  {h.text ? (
                    <q className="text-sm break-words">{h.text}</q>
                  ) : (
                    <p className={cn("text-sm", FAINT_TEXT)}>Text {h.gone ?? "not kept"}.</p>
                  )}
                  <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                    {h.author && <span>{h.author}</span>}
                    <span className="font-mono">{h.id}</span>
                    <span>·</span>
                    <span className="text-foreground">{hunchOutcome(h, rows)}</span>
                    {h.links.map((l) => (
                      <button
                        key={l.opportunity}
                        type="button"
                        onClick={() => onOpen(l.opportunity)}
                        className={cn("rounded font-mono underline decoration-dotted", FOCUS_RING)}
                      >
                        {l.opportunity}
                      </button>
                    ))}
                  </p>
                </div>
                {canAct && !h.gone && mayWithdraw(h) && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onWithdraw(h.id)}
                    data-testid={`hunch-withdraw-${h.id}`}
                  >
                    Withdraw
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section
        aria-labelledby="hunch-add-h"
        className="flex h-fit flex-col gap-2 rounded-xl border border-border bg-card p-4"
      >
        <h2 id="hunch-add-h" className="text-base font-semibold">
          Add a hunch
        </h2>
        <p className="text-sm text-muted-foreground">
          A hunch is a person's conviction, stored next to the evidence with weight 0.1. Scout looks
          for evidence and proposes the cheapest test. The ledger later shows how often hunches were
          right.
        </p>
        {canAct ? (
          <HunchComposer id="hunches" onAdd={onAdd} />
        ) : (
          <p className="text-sm text-muted-foreground">Ask an operator to add one.</p>
        )}
      </section>
    </div>
  );
}
