/**
 * Teams › Bench: each role's track record by vendor and model over 30 days,
 * always with n. Rows under five samples are grey and say "not enough data".
 * On a phone the table becomes a card list.
 */

import type { BenchResponse, BenchRowView } from "@/lib/factoryTeamsApi";
import { cn } from "@/lib/utils";
import { ROLE_WORD } from "./teamsModel";
import { QualityBar, benchModel, money, pct } from "./TrackRecord";

function roleName(role: string): string {
  return ROLE_WORD[role] ?? role;
}

function costText(row: BenchRowView): string {
  if (!row.cost) return "—";
  return row.cost.per_unit != null
    ? `${money(row.cost.per_unit)} per ${row.cost.unit}`
    : `${money(row.cost.usd)} in all`;
}

function Quality({ row }: { row: BenchRowView }) {
  if (!row.quality) {
    return <span className="text-xs text-muted-foreground">no quality measure yet</span>;
  }
  return (
    <div className="flex min-w-36 flex-col gap-1">
      <div className="flex items-center gap-2">
        <QualityBar value={row.quality.value} low={row.low_n} />
        <span className="w-10 shrink-0 text-right font-mono text-xs tabular-nums">
          {pct(row.quality.value)}
        </span>
      </div>
      <span className="text-xs text-muted-foreground">
        {row.quality.label}
        {row.low_n ? " · not enough data" : ""}
      </span>
    </div>
  );
}

export function BenchTable({ bench }: { bench: BenchResponse }) {
  const rows = bench.rows;
  return (
    <section
      aria-labelledby="bench-title"
      data-testid="bench-table"
      className="rounded-xl border border-border bg-card"
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-4 py-3">
        <h2 id="bench-title" className="grow text-ui font-semibold">
          Track record by role and model
        </h2>
        <span className="text-xs text-muted-foreground">
          last {bench.window.label === "30d" ? "30 days" : bench.window.label} · always with n ·
          grey rows have fewer than {bench.low_n} samples
        </span>
      </header>
      {bench.note && (
        <p
          data-testid="bench-note"
          className="border-b border-border px-4 py-2 text-sm text-muted-foreground"
        >
          {bench.note}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground" data-testid="bench-empty">
          No finished work or spend in this window yet.
        </p>
      ) : (
        <>
          <table className="hidden w-full text-sm sm:table">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2 font-medium">
                  Role
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  Vendor · model
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  n
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  Quality
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  Rounds
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={`${row.role}-${row.vendor}-${row.model}`}
                  data-low={row.low_n || undefined}
                  className={cn(
                    "border-t border-border align-top",
                    row.low_n && "text-muted-foreground",
                  )}
                >
                  <td className="px-4 py-2.5">{roleName(row.role)}</td>
                  <td className="px-2 py-2.5 font-mono text-xs">{benchModel(row)}</td>
                  <td className="px-2 py-2.5 font-mono tabular-nums">{row.n}</td>
                  <td className="px-2 py-2.5">
                    <Quality row={row} />
                  </td>
                  <td className="px-2 py-2.5 font-mono tabular-nums">{row.rounds?.value ?? "—"}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{costText(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="flex flex-col sm:hidden" aria-label="Track record by role and model">
            {rows.map((row) => (
              <li
                key={`${row.role}-${row.vendor}-${row.model}`}
                data-low={row.low_n || undefined}
                className={cn(
                  "flex flex-col gap-1.5 border-t border-border px-4 py-3 first:border-t-0",
                  row.low_n && "text-muted-foreground",
                )}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{roleName(row.role)}</span>
                  <span className="font-mono text-xs">n {row.n}</span>
                </div>
                <span className="font-mono text-xs">{benchModel(row)}</span>
                <Quality row={row} />
                <span className="font-mono text-xs">
                  {costText(row)}
                  {row.rounds ? ` · ${row.rounds.value} review rounds` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <footer className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        Compare like with like: the same role on similar projects. Quality counts only work the
        server stamped with its vendor and model.
      </footer>
    </section>
  );
}
