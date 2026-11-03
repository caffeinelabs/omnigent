/**
 * A project's team drawn as a small org chart: the project lead on top, a
 * connector to three groups (Build, Check, Run), group widths in proportion
 * to their members so tiles sit side by side. The editor draws the same chart
 * with every group (equal widths, tiles stacked) and a ghost tile at the foot
 * of each. Under 1024 px the groups stack, each with its label.
 */

import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { type TeamGroupInfo, chartGroups } from "./teamModel";

export interface TeamChartProps<T extends { role: string }> {
  members: readonly T[];
  tileKey: (member: T) => string;
  renderTile: (member: T) => ReactNode;
  /** The editor: every group shows, with `renderGhost` at its foot. */
  editing?: boolean;
  renderGhost?: (group: TeamGroupInfo) => ReactNode;
  className?: string;
}

export function TeamChart<T extends { role: string }>({
  members,
  tileKey,
  renderTile,
  editing = false,
  renderGhost,
  className,
}: TeamChartProps<T>) {
  const { lead, groups } = chartGroups(members);
  const shown = groups.filter((g) => editing || g.members.length > 0);
  const weights = shown.map((g) => (editing ? 1 : Math.max(1, g.members.length)));
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  const left = `${((weights[0] ?? 1) / 2 / total) * 100}%`;
  const right = `${((weights.at(-1) ?? 1) / 2 / total) * 100}%`;
  return (
    <div data-testid="team-chart" className={cn("flex min-w-0 flex-col", className)}>
      {lead.length > 0 && (
        <div
          className={cn(
            "relative mx-auto flex w-full flex-col items-stretch gap-2 pb-4 lg:pb-6",
            editing ? "lg:w-[19rem]" : "lg:w-[15rem]",
            // The connector down to the groups' bar.
            shown.length > 0 &&
              "lg:after:absolute lg:after:bottom-0 lg:after:left-1/2 lg:after:h-6 lg:after:w-px lg:after:bg-border-strong",
          )}
        >
          {lead.map((m) => (
            <div key={tileKey(m)} className="flex min-w-0 flex-col">
              {renderTile(m)}
            </div>
          ))}
        </div>
      )}
      {shown.length > 0 && (
        <div
          className="relative grid min-w-0 grid-cols-1 gap-x-3 gap-y-5 lg:grid-cols-[var(--team-cols)] lg:pt-6"
          style={
            {
              "--team-cols": weights.map((w) => `minmax(0,${w}fr)`).join(" "),
            } as CSSProperties
          }
        >
          {lead.length > 0 && (
            <span
              aria-hidden
              className="absolute top-0 hidden h-px bg-border-strong lg:block"
              style={{ left, right }}
            />
          )}
          {shown.map(({ info, members: list }) => (
            <section
              key={info.key}
              aria-label={info.label}
              data-testid={`team-group-${info.key}`}
              className={cn(
                "relative flex min-w-0 flex-col gap-2",
                lead.length > 0 &&
                  "lg:before:absolute lg:before:-top-6 lg:before:left-1/2 lg:before:h-6 lg:before:w-px lg:before:bg-border-strong",
              )}
            >
              <h3 className="text-center text-[11px] font-semibold tracking-wider text-muted-foreground uppercase max-lg:text-left">
                {info.label}
              </h3>
              <div
                className={cn(
                  "grid w-full min-w-0 gap-2.5",
                  editing
                    ? "grid-cols-1 sm:max-lg:grid-cols-2"
                    : "grid-cols-[repeat(auto-fit,minmax(min(100%,9.5rem),1fr))] lg:mx-auto lg:max-w-[var(--team-tiles-max)]",
                )}
                // Tiles stay compact (about 15rem) and centred under their group's label.
                style={
                  editing
                    ? undefined
                    : ({
                        "--team-tiles-max": `calc(${list.length} * 15rem + ${list.length - 1} * 0.625rem)`,
                      } as CSSProperties)
                }
              >
                {list.map((m) => (
                  <div key={tileKey(m)} className="flex min-w-0 flex-col">
                    {renderTile(m)}
                  </div>
                ))}
                {editing && renderGhost?.(info)}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
