/**
 * Projects per derived stage across the pipeline; clicking a stage shows only
 * its cards, clicking it again (or Clear) shows all.
 */

import { Fragment } from "react";
import { StarIcon, XIcon } from "lucide-react";
import type { ProjectView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { STAGE_LABEL } from "../factoryFormat";
import { BOARD_STAGES, type BoardStage, stageCounts } from "./boardModel";

export function StageStrip({
  projects,
  filter,
  onFilter,
}: {
  projects: ProjectView[];
  filter: BoardStage | null;
  onFilter: (stage: BoardStage | null) => void;
}) {
  const counts = stageCounts(projects);
  return (
    <div className="flex min-w-0 items-center gap-2">
      <ol
        data-testid="factory-stage-strip"
        aria-label="Projects by stage"
        className="flex min-w-0 flex-wrap items-center gap-y-1 text-sm"
      >
        {BOARD_STAGES.map((stage, i) => {
          const n = counts[stage];
          const active = filter === stage;
          const ready = stage === "ready" && n > 0;
          return (
            <Fragment key={stage}>
              {i > 0 && <li aria-hidden className="mx-0.5 h-px w-3 bg-border max-sm:hidden" />}
              <li>
                <button
                  type="button"
                  data-testid={`stage-filter-${stage}`}
                  data-stage={stage}
                  data-count={n}
                  aria-pressed={active}
                  disabled={n === 0 && !active}
                  onClick={() => onFilter(active ? null : stage)}
                  className={cn(
                    "inline-flex h-6 cursor-pointer items-center gap-1 rounded-md px-1.5 transition-colors disabled:cursor-default",
                    n === 0
                      ? "text-muted-foreground/50"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    ready && "text-success hover:text-success",
                    active && "bg-muted text-foreground ring-1 ring-border",
                  )}
                >
                  {ready && <StarIcon aria-hidden className="size-3 fill-current" />}
                  {STAGE_LABEL[stage]}
                  <span
                    className={cn(
                      "tabular-nums",
                      n > 0 && !ready && "text-foreground",
                      ready && "font-medium",
                    )}
                  >
                    {n}
                  </span>
                </button>
              </li>
            </Fragment>
          );
        })}
      </ol>
      {filter && (
        <button
          type="button"
          onClick={() => onFilter(null)}
          data-testid="stage-filter-clear"
          className="inline-flex h-6 shrink-0 cursor-pointer items-center gap-0.5 rounded-md px-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <XIcon aria-hidden className="size-3" />
          Clear
        </button>
      )}
    </div>
  );
}
