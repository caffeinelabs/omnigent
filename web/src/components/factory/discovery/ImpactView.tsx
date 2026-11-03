/**
 * Discovery's Impact: one card per readout (opportunity → bet → shipped →
 * readout, the predicted band and the actual dot, metric, method, decision)
 * and one per bet still waiting for its readout (the band only, "not
 * measured yet"); beside them the prediction ledger and the hit rate by
 * origin. Requested work is "not measured", never a success; every rate
 * shows its n and says "not enough data" when it is low.
 */

import { cn } from "@/lib/utils";
import { signedText } from "../cards/betModel";
import { RangeBar } from "../ui/RangeBar";
import { FOCUS_RING, MEANING, READOUT_MARK, READOUT_WORD } from "../ui/statusTokens";
import type { LedgerFacts, PendingBetCard, ReadoutView } from "./discoveryModel";

function Bar({ part, whole }: { part: number; whole: number }) {
  const pct = whole > 0 ? Math.round((part / whole) * 100) : 0;
  return (
    <span aria-hidden className="block h-1.5 w-full overflow-hidden rounded-full bg-status-gray/20">
      <span className="block h-full rounded-full bg-status-blue" style={{ width: `${pct}%` }} />
    </span>
  );
}

function VerdictChip({ readout }: { readout: ReadoutView }) {
  if (!readout.verdict) return null;
  const tone =
    readout.verdict === "won"
      ? MEANING.working
      : readout.verdict === "lost"
        ? MEANING.broken
        : MEANING.idle;
  return (
    <span
      data-testid={`readout-verdict-${readout.id}`}
      className={cn("rounded-md px-1.5 text-sm font-medium", tone.surface, tone.text)}
    >
      {READOUT_MARK[readout.verdict]} {READOUT_WORD[readout.verdict]}
      {readout.day != null ? ` · day ${readout.day}` : ""}
    </span>
  );
}

function ReadoutCard({ readout, onOpen }: { readout: ReadoutView; onOpen: (id: string) => void }) {
  const decision = readout.decision?.decision;
  return (
    <article
      data-testid={`impact-readout-${readout.id}`}
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4"
    >
      <header className="flex flex-wrap items-start gap-2">
        <h3 className="min-w-0 grow text-base font-semibold">
          <button
            type="button"
            onClick={() => onOpen(readout.id)}
            className={cn("rounded text-left hover:underline", FOCUS_RING)}
          >
            {readout.title}
          </button>
        </h3>
        <VerdictChip readout={readout} />
      </header>
      <p className="font-mono text-xs text-muted-foreground">
        {[readout.opportunity, readout.proposal, "shipped", "readout"].filter(Boolean).join(" → ")}
      </p>
      {readout.low != null && readout.high != null && (
        <RangeBar
          low={readout.low}
          high={readout.high}
          actual={readout.actual}
          unit={readout.unit}
          verdict={readout.verdict}
          format={signedText}
          legend
        />
      )}
      <p className="text-sm text-muted-foreground">
        {[
          readout.metric ? `Metric: ${readout.metric}` : null,
          readout.method ? `method: ${readout.method.replace(/_/g, " ")}` : null,
          decision ? `decision: ${decision}` : "decision: not yet",
          readout.nextDay != null ? `next: day ${readout.nextDay}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
    </article>
  );
}

function PendingCard({
  bet,
  onOpenBet,
}: {
  bet: PendingBetCard;
  onOpenBet: (ref: string) => void;
}) {
  return (
    <article
      data-testid={`impact-bet-${bet.proposal}`}
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4"
    >
      <header className="flex flex-wrap items-start gap-2">
        <h3 className="min-w-0 grow text-base font-semibold">
          <button
            type="button"
            onClick={() => onOpenBet(bet.proposal)}
            className={cn("rounded text-left hover:underline", FOCUS_RING)}
          >
            {bet.title}
          </button>
        </h3>
        <span
          className={cn(
            "rounded-md px-1.5 text-sm font-medium",
            bet.waits
              ? cn(MEANING.needsYou.surface, MEANING.needsYou.text)
              : "bg-muted text-foreground",
          )}
        >
          {bet.state}
        </span>
      </header>
      <p className="font-mono text-xs text-muted-foreground">
        {[bet.opportunity, bet.proposal, bet.project ?? "no project yet", "readout after release"]
          .filter(Boolean)
          .join(" → ")}
      </p>
      <RangeBar low={bet.low} high={bet.high} unit={bet.unit} format={signedText} />
      <p className="text-sm text-muted-foreground">
        predicted {signedText(Math.min(bet.low, bet.high))} to{" "}
        {signedText(Math.max(bet.low, bet.high))} {bet.unit}
        {bet.metric ? ` · ${bet.metric}` : ""} · not measured yet
      </p>
    </article>
  );
}

export function ImpactView({
  readouts,
  readoutsRoute,
  bets,
  ledger,
  onOpenReadout,
  onOpenBet,
}: {
  readouts: readonly ReadoutView[];
  /** The server writes readouts (its readouts route answered). */
  readoutsRoute: boolean;
  bets: readonly PendingBetCard[];
  ledger: LedgerFacts;
  onOpenReadout: (id: string) => void;
  onOpenBet: (ref: string) => void;
}) {
  const empty = readouts.length === 0 && bets.length === 0;
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="flex min-w-0 flex-col gap-3" data-testid="discovery-impact">
        {empty ? (
          <section className="rounded-xl border border-border bg-card p-4 text-sm">
            <h2 className="text-base font-semibold">Nothing measured yet</h2>
            <p className="mt-1 text-muted-foreground">
              Every bet fixes a metric and a predicted range when it is filed. Readouts arrive on
              day 7 and day 28 after release. Requested work ships with no metric and shows as "not
              measured", never as a success.
            </p>
          </section>
        ) : (
          <>
            {readouts.map((r) => (
              <ReadoutCard key={r.id} readout={r} onOpen={onOpenReadout} />
            ))}
            {bets.map((b) => (
              <PendingCard key={b.proposal} bet={b} onOpenBet={onOpenBet} />
            ))}
          </>
        )}
        {!readoutsRoute && (
          <p data-testid="impact-no-readouts" className="text-sm text-muted-foreground">
            This server does not write readouts yet, so every bet shows as not measured.
          </p>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-3">
        <section
          aria-labelledby="ledger-h"
          data-testid="impact-ledger"
          className="flex flex-col gap-1.5 rounded-xl border border-border bg-card p-4 text-sm"
        >
          <h2 id="ledger-h" className="text-base font-semibold">
            Prediction ledger
          </h2>
          <p>
            {ledger.readouts} {ledger.readouts === 1 ? "readout" : "readouts"} · {ledger.inside}{" "}
            inside the predicted range
          </p>
          <p className="text-muted-foreground">Win rate by evidence level: {ledger.levelLine}.</p>
          <p className="text-muted-foreground">
            Each bet is stamped with the definition version that ranked it, so rubric versions can
            be compared once readouts pile up.
          </p>
        </section>
        <section
          aria-labelledby="origin-h"
          data-testid="impact-origin"
          className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 text-sm"
        >
          <h2 id="origin-h" className="text-base font-semibold">
            Hit rate by origin
          </h2>
          <div className="flex justify-between gap-2">
            <span>Discovered work</span>
            <span className="font-mono">
              {ledger.discovered.total
                ? `${ledger.discovered.won} of ${ledger.discovered.total} won`
                : "nothing measured yet"}
            </span>
          </div>
          <Bar part={ledger.discovered.won} whole={ledger.discovered.total} />
          <div className="flex justify-between gap-2">
            <span>Requested work</span>
            <span className="font-mono text-muted-foreground">
              {ledger.requested.count} in flight or shipped, {ledger.requested.measured} measured
            </span>
          </div>
          <Bar part={ledger.requested.measured} whole={Math.max(1, ledger.requested.count)} />
          <p className="text-muted-foreground">
            Requested work shows as "not measured", never as a success. Add a metric to a requested
            project to learn from it.
          </p>
        </section>
      </div>
    </div>
  );
}
