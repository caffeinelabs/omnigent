/**
 * Home's What users need: the top opportunities in their Now and Next bands,
 * ranked from evidence with the factory's own rubric (rank, title, evidence
 * meter, accounts, status, score; "Waits for you" in amber only where its bet
 * waits on the viewer), then a box for a person's hunch (for people who may
 * write, once the server answers discovery). Votes and pins never change a
 * score. While the factory has gathered nothing yet it says so.
 */

import { ChevronRightIcon, LightbulbIcon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { discoveryPageHref } from "../factoryRoutes";
import { EvidenceMeter } from "../ui/EvidenceMeter";
import { useFactoryToast } from "../ui/Toast";
import { FAINT_TEXT, FOCUS_RING, MEANING } from "../ui/statusTokens";
import type { OpportunityRow } from "./homeModel";
import { postHunch } from "./homeQueries";

const BAND_WORD = { now: "Now", next: "Next", later: "Later", parked: "Parked" } as const;

function OpportunityLine({
  factoryId,
  row,
  rank,
  waitsForYou,
}: {
  factoryId: string;
  row: OpportunityRow;
  rank: number;
  waitsForYou: boolean;
}) {
  return (
    <li className="border-t border-border first:border-t-0">
      <Link
        to={`${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(row.id)}`}
        componentId="factory.home.users.opportunity"
        data-testid={`home-opportunity-${row.id}`}
        className={cn(
          "grid grid-cols-[1.25rem_minmax(0,1fr)_auto] gap-x-2 px-4 py-2.5 hover:bg-muted/60",
          FOCUS_RING,
        )}
      >
        <span className="text-sm text-muted-foreground tabular-nums">{rank}</span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-foreground">{row.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            {row.level && <EvidenceMeter level={row.level} />}
            {row.accounts != null && <span>{row.accounts} accounts</span>}
            {row.status && <span>{row.status}</span>}
            {row.band && <span className={FAINT_TEXT}>{BAND_WORD[row.band]}</span>}
          </span>
        </span>
        <span className="flex flex-col items-end gap-1">
          {row.score != null && (
            <span className="font-mono text-sm tabular-nums" title="score">
              {row.score.toFixed(2)}
            </span>
          )}
          {waitsForYou && (
            <span
              className={cn(
                "rounded-md px-1.5 text-xs font-medium",
                MEANING.needsYou.surface,
                MEANING.needsYou.text,
              )}
            >
              Waits for you
            </span>
          )}
        </span>
      </Link>
    </li>
  );
}

function HunchBox({ factoryId }: { factoryId: string }) {
  const toast = useFactoryToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const hunch = text.trim();
    if (!hunch || busy) return;
    setBusy(true);
    postHunch(factoryId, hunch).then(
      () => {
        setText("");
        setBusy(false);
        toast.show({ message: "Hunch added. It counts as a hunch, never as user evidence." });
      },
      (error: unknown) => {
        setBusy(false);
        toast.show({
          message: `Could not add the hunch: ${error instanceof Error ? error.message : String(error)}`,
        });
      },
    );
  };
  return (
    <form
      onSubmit={submit}
      data-testid="home-hunch"
      className="flex flex-wrap gap-2 border-t border-border px-4 py-3"
    >
      <label htmlFor="home-hunch-input" className="sr-only">
        Add a hunch
      </label>
      <Input
        id="home-hunch-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="I think … because …"
        autoComplete="off"
        className="h-8 min-w-0 flex-1 basis-48"
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy || !text.trim()}>
        <LightbulbIcon aria-hidden className="size-3.5" />
        Add hunch
      </Button>
    </form>
  );
}

export function WhatUsersNeed({
  factoryId,
  rows,
  total,
  loading,
  defVersion,
  betsForYou,
  canHunch,
  className,
}: {
  factoryId: string;
  /** The top of the ranked list (null while the ledger has nothing to say). */
  rows: readonly OpportunityRow[] | null;
  /** Opportunities whose bet is a row of Needs you (the one amber chip comes from there). */
  betsForYou: ReadonlySet<string>;
  total: number | null;
  loading: boolean;
  defVersion: number | null;
  /** Show the hunch box: the viewer may write and the server takes hunches. */
  canHunch: boolean;
  className?: string;
}) {
  return (
    <section
      aria-labelledby="home-users-h"
      data-testid="home-users"
      className={cn("rounded-xl border border-border bg-card", className)}
    >
      <header className="flex items-center gap-2 px-4 pt-3 pb-1">
        <h2 id="home-users-h" className="grow text-base font-semibold">
          What users need
        </h2>
        <Link
          to={discoveryPageHref(factoryId)}
          componentId="factory.home.users.all"
          className={cn(
            "flex items-center gap-0.5 rounded text-xs font-medium text-muted-foreground hover:text-foreground",
            FOCUS_RING,
          )}
        >
          All{total ? ` ${total}` : ""}
          <ChevronRightIcon aria-hidden className="size-3.5" />
        </Link>
      </header>
      <p className="px-4 pb-2 text-xs text-muted-foreground">
        Ranked from evidence with the rubric
        {defVersion != null ? ` in definition v${defVersion}` : ""}. Votes and pins never change a
        score.
      </p>
      {loading && rows == null ? (
        <div aria-busy="true" className="mx-4 mb-3 h-24 animate-pulse rounded-lg bg-muted" />
      ) : rows && rows.length > 0 ? (
        <ol className="m-0 list-none border-t border-border p-0">
          {rows.map((row, i) => (
            <OpportunityLine
              key={row.id}
              factoryId={factoryId}
              row={row}
              rank={i + 1}
              waitsForYou={betsForYou.has(row.id)}
            />
          ))}
        </ol>
      ) : (
        <p
          data-testid="home-users-empty"
          className="border-t border-border px-4 py-3 text-sm text-muted-foreground"
        >
          No opportunities yet. The discovery lead groups signals into opportunities in its weekly
          synthesis.
        </p>
      )}
      {canHunch && <HunchBox factoryId={factoryId} />}
    </section>
  );
}
