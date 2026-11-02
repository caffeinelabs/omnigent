/**
 * An agent's track record over 30 days: its role on its own vendor and model
 * first, then the same role on every vendor and model the factory ran, as
 * bars with n. Fewer than five samples is grey and says "not enough data".
 * Until work is stamped with its model, it says so and shows cost only.
 */

import type { BenchResponse, BenchRowView } from "@/lib/factoryTeamsApi";
import { cn } from "@/lib/utils";
import { DATA_HUE } from "../ui/statusTokens";

export function pct(value: number): string {
  return `${Math.round(value * 100)} %`;
}

export function money(usd: number | null | undefined): string {
  if (usd == null) return "—";
  return usd >= 100 ? `$${Math.round(usd)}` : `$${usd.toFixed(2)}`;
}

/** `claude · sonnet` of a bench row. */
export function benchModel(row: Pick<BenchRowView, "vendor" | "model">): string {
  return `${row.vendor ?? "agent"} · ${row.model ?? "default"}`;
}

export function QualityBar({ value, low }: { value: number; low: boolean }) {
  return (
    <span aria-hidden className="relative block h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <span
        className="absolute inset-y-0 left-0 rounded-full"
        style={{
          width: `${Math.max(0, Math.min(1, value)) * 100}%`,
          background: low ? "var(--status-gray)" : DATA_HUE,
        }}
      />
    </span>
  );
}

export function TrackRecord({
  role,
  vendor,
  model,
  bench,
  loading,
}: {
  role: string;
  vendor: string | null;
  model: string | null;
  bench: BenchResponse | undefined;
  loading?: boolean;
}) {
  if (loading && !bench) {
    return <div aria-busy="true" className="h-16 animate-pulse rounded bg-muted" />;
  }
  const rows = (bench?.rows ?? []).filter((r) => r.role === role);
  const own = rows.find((r) => r.vendor === vendor && r.model === model) ?? null;
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="track-record-empty">
        Not enough finished work yet.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3" data-testid="track-record">
      {own && (
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {own.quality && (
            <div>
              <div
                className={cn(
                  "text-lg font-semibold tabular-nums",
                  own.low_n && "text-muted-foreground",
                )}
              >
                {pct(own.quality.value)}
              </div>
              <div className="text-xs text-muted-foreground">
                {own.quality.label} · n {own.quality.n}
                {own.low_n ? " · not enough data" : ""}
              </div>
            </div>
          )}
          {own.rounds && (
            <div>
              <div className="text-lg font-semibold tabular-nums">{own.rounds.value}</div>
              <div className="text-xs text-muted-foreground">review rounds · n {own.rounds.n}</div>
            </div>
          )}
          {own.cost?.per_unit != null && (
            <div>
              <div className="font-mono text-lg font-semibold tabular-nums">
                {money(own.cost.per_unit)}
              </div>
              <div className="text-xs text-muted-foreground">per {own.cost.unit}</div>
            </div>
          )}
        </div>
      )}
      {bench?.note && <p className="text-xs text-muted-foreground">{bench.note}</p>}
      <div>
        <p className="text-xs text-muted-foreground">
          The same role on each vendor and model (its own is first)
        </p>
        <ul className="mt-1.5 flex flex-col gap-1.5">
          {[...rows]
            .sort((a, b) => Number(b === own) - Number(a === own))
            .map((row) => (
              <li
                key={`${row.vendor}-${row.model}`}
                data-low={row.low_n || undefined}
                className={cn(
                  "grid grid-cols-[minmax(0,11rem)_1fr_auto] items-center gap-2 text-xs",
                  row.low_n && "text-muted-foreground",
                )}
              >
                <span className="truncate">
                  <span className={cn("font-mono", row === own && "font-semibold")}>
                    {benchModel(row)}
                  </span>
                  {row === own ? " (its own)" : ""}
                </span>
                {row.quality ? <QualityBar value={row.quality.value} low={row.low_n} /> : <span />}
                <span className="w-24 text-right font-mono tabular-nums">
                  {row.quality
                    ? `${pct(row.quality.value)} `
                    : `${money(row.cost?.per_unit ?? row.cost?.usd)} `}
                  n {row.n}
                </span>
              </li>
            ))}
        </ul>
      </div>
    </div>
  );
}
