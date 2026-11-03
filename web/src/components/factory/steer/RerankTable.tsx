/**
 * "Effect on the ranking": how a strategy or rubric change moves the open
 * opportunities, from the server's preview (band moves first, then positions,
 * with the score before → after). Opportunity ids open their panel.
 */

import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { discoveryPageHref } from "../factoryRoutes";
import { MEANING } from "../ui/statusTokens";
import { BAND_LABEL, type RerankRow, rankText } from "./steerModel";

export const RERANK_ROWS_MAX = 8;

export function RerankTable({
  factoryId,
  rows,
  discoveryOn,
  loading,
}: {
  factoryId: string;
  rows: readonly RerankRow[];
  discoveryOn: boolean;
  loading: boolean;
}) {
  if (!discoveryOn) {
    return (
      <p data-testid="rerank-off" className="text-sm text-muted-foreground">
        Discovery is off, so there are no opportunities to re-rank. The change still applies.
      </p>
    );
  }
  if (loading && rows.length === 0) {
    return <div aria-busy="true" className="h-16 animate-pulse rounded-md bg-muted/70" />;
  }
  if (rows.length === 0) {
    return (
      <p data-testid="rerank-none" className="text-sm text-muted-foreground">
        No opportunity changes rank.
      </p>
    );
  }
  const shown = rows.slice(0, RERANK_ROWS_MAX);
  return (
    <table data-testid="rerank-table" className="w-full text-sm">
      <caption className="sr-only">How the opportunities re-rank</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Opportunity</th>
          <th scope="col">Title</th>
          <th scope="col">Move</th>
        </tr>
      </thead>
      <tbody>
        {shown.map((row) => {
          const band = row.move != null && row.before.band && row.after.band;
          return (
            <tr
              key={row.ref}
              data-testid={`rerank-${row.ref}`}
              className="border-t border-border/60 align-top"
            >
              <td className="py-1.5 pr-2 font-mono text-xs whitespace-nowrap">
                <Link
                  to={`${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(row.ref)}`}
                  componentId="factory.steer.rerank_ref"
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  {row.ref}
                </Link>
              </td>
              <td className="py-1.5 pr-2">{row.title}</td>
              <td className="py-1.5 text-right font-mono text-xs whitespace-nowrap">
                {band ? (
                  <span
                    className={cn(
                      "inline-flex items-center gap-0.5",
                      row.move === "up" ? MEANING.working.text : "text-muted-foreground",
                    )}
                  >
                    {row.move === "up" ? (
                      <ArrowUpIcon aria-label="up" className="size-3" />
                    ) : (
                      <ArrowDownIcon aria-label="down" className="size-3" />
                    )}
                    {BAND_LABEL[row.before.band!]} → {BAND_LABEL[row.after.band!]}
                  </span>
                ) : (
                  <span>
                    #{row.before.position} → #{row.after.position}
                  </span>
                )}
                <br />
                <span className="text-muted-foreground">
                  {rankText(row.before.rank)} → {rankText(row.after.rank)}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
      {rows.length > shown.length && (
        <tfoot>
          <tr>
            <td colSpan={3} className="pt-1 text-xs text-muted-foreground">
              and {rows.length - shown.length} more
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  );
}
