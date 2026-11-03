/**
 * A project's team history: every definition version that changed its team,
 * newest first, with who made it, when, what changed, and whether it was
 * undone or restored an earlier one. Every earlier team can be restored;
 * restoring stages the change in the team editor, where it is reviewed and
 * applied (and can be undone) like any other. There is one version stream:
 * these are definition versions, never a separate team version.
 */

import { HistoryIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatElapsed } from "../factoryFormat";
import { EmptyState } from "../project/projectParts";
import type { RestoredTeam, StagedTeamChange, TeamHistoryRow } from "./teamModel";
import { restoreChange } from "./teamModel";

export interface TeamHistoryProps {
  rows: readonly TeamHistoryRow[];
  /** The project's own team in force (`null`: it follows today's bundle). */
  current: RestoredTeam | null;
  nowS: number;
  loading?: boolean;
  /** Member id → the name the chart shows (history lines name members by id). */
  names?: ReadonlyMap<string, string>;
  /** Stage a restore (absent when the viewer cannot change the team). */
  onRestore?: (change: Extract<StagedTeamChange, { kind: "restore" }>) => void;
}

export function TeamHistory({ rows, current, nowS, loading, names, onRestore }: TeamHistoryProps) {
  const named = (line: string) =>
    names
      ? line.replace(/\bM-\d+\b/g, (id) => (names.has(id) ? `${names.get(id)} (${id})` : id))
      : line;
  if (loading) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true" data-testid="team-history-loading">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-14 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3" data-testid="team-history">
      {rows.length === 0 ? (
        <EmptyState
          testId="team-history-empty"
          icon={<HistoryIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No team changes yet"
          body="This project still runs the team it started with. Each change you apply becomes a definition version and shows here."
        />
      ) : (
        <ol className="flex flex-col divide-y divide-border">
          {rows.map((row) => {
            const restore = onRestore ? restoreChange(row, current) : null;
            const ago = formatElapsed(nowS - row.createdAt);
            return (
              <li
                key={row.version}
                data-testid={`team-history-v${row.version}`}
                className="flex items-start gap-3 py-2.5"
              >
                <span className="w-9 shrink-0 pt-0.5 font-mono text-xs text-muted-foreground">
                  v{row.version}
                </span>
                <div className="min-w-0 grow">
                  <ul className="text-sm">
                    {row.lines.map((line) => (
                      <li key={line} className="break-words">
                        {named(line)}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.by}
                    {ago ? ` · ${ago} ago` : ""}
                    {row.proposalRef ? ` · ${row.proposalRef}` : ""}
                    {row.restoredFrom != null ? ` · restores v${row.restoredFrom}` : ""}
                    {row.undoneBy ? " · undone" : ""}
                  </p>
                </div>
                {row.current && !row.undoneBy ? (
                  <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-xs text-foreground/80">
                    current
                  </span>
                ) : restore ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    componentId="factory.team.history_restore"
                    onClick={() => onRestore?.(restore)}
                  >
                    Restore
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      <p className="rounded-lg bg-muted/60 px-3 py-2 text-xs text-foreground/80">
        Restoring is itself a staged change you review, and you can undo it. Money already spent and
        commits already pushed do not roll back.
      </p>
    </div>
  );
}
