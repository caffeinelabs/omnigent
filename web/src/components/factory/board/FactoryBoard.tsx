/**
 * The Board tab: what needs the human, the pipeline counts (a filter), the
 * project cards in board order and the finished projects folded into Done.
 */

import { type ReactNode, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { CircleCheckIcon, ChevronRightIcon, EyeOffIcon, FolderKanbanIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { STAGE_LABEL, plural } from "../factoryFormat";
import { type BoardStage, boardSections, boardStage, needsYou } from "./boardModel";
import { NeedsYouStrip } from "./NeedsYouStrip";
import { DoneProjectRow, ProjectCard } from "./ProjectCard";
import { StageStrip } from "./StageStrip";

export interface FactoryBoardProps {
  snapshot: FactorySnapshot;
  nowS: number;
  onReview: (number: number) => void;
  /** Point at one decision (a blocked card's `D-n`). */
  onDecision: (ref: string) => void;
  /** The Needs-you decisions chip. */
  onDecisions: () => void;
  /** Scroll to and flash an element by DOM id. */
  onJump: (targetId: string) => void;
  /** Narrow screens: the decisions rail folded under the Needs-you strip. */
  decisionsPanel?: ReactNode;
  decisionsExpanded?: boolean;
}

/** PR ids that were not on the board when it first rendered. */
function useNewPrIds(snapshot: FactorySnapshot): ReadonlySet<string> {
  const initial = useRef<Set<string> | null>(null);
  if (initial.current === null) initial.current = new Set(snapshot.prs.map((pr) => pr.id));
  const seen = initial.current;
  return new Set(snapshot.prs.filter((pr) => !seen.has(pr.id)).map((pr) => pr.id));
}

export function FactoryBoard({
  snapshot,
  nowS,
  onReview,
  onDecision,
  onDecisions,
  onJump,
  decisionsPanel,
  decisionsExpanded,
}: FactoryBoardProps) {
  const [filter, setFilter] = useState<BoardStage | null>(null);
  const newPrIds = useNewPrIds(snapshot);
  const needs = needsYou(snapshot);
  const shown = filter
    ? snapshot.projects.filter((p) => boardStage(p.stage) === filter)
    : snapshot.projects;
  const { active, done } = boardSections(shown);
  const empty = snapshot.projects.length === 0;
  // Nothing left in flight: the Done group is the board, so it opens.
  const allDone = !filter && active.length === 0 && done.length > 0;
  // A chip's target may sit in a card the stage filter hides: show every card first.
  const jump = (targetId: string) => {
    if (filter && !document.getElementById(targetId)) flushSync(() => setFilter(null));
    onJump(targetId);
  };
  return (
    <div data-testid="factory-board" className="flex min-w-0 flex-col gap-4">
      <NeedsYouStrip
        needs={needs}
        onJump={jump}
        onDecisions={onDecisions}
        decisionsPanel={decisionsPanel}
        decisionsExpanded={decisionsExpanded}
      />
      {!empty && <StageStrip projects={snapshot.projects} filter={filter} onFilter={setFilter} />}

      {empty && (
        <div
          data-testid="factory-board-empty"
          className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-5 py-10 text-center"
        >
          <FolderKanbanIcon aria-hidden className="size-6 text-muted-foreground/50" />
          <p className="text-ui font-medium">No projects yet</p>
          <p className="max-w-prose text-sm text-muted-foreground">
            The lead files each project here as it starts its project lead. Add one from the header.
          </p>
        </div>
      )}

      {active.length > 0 && (
        <div className="flex min-w-0 flex-col gap-3" data-testid="factory-cards">
          {active.map((project) => (
            <ProjectCard
              key={project.slug}
              snapshot={snapshot}
              project={project}
              nowS={nowS}
              onReview={onReview}
              onDecision={onDecision}
              newPrIds={newPrIds}
            />
          ))}
        </div>
      )}

      {filter && active.length === 0 && done.length === 0 && (
        <p className="text-sm text-muted-foreground">No projects in {STAGE_LABEL[filter]}.</p>
      )}

      {allDone && (
        <p
          data-testid="factory-all-done"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground"
        >
          <CircleCheckIcon aria-hidden className="size-3.5 text-success" />
          {done.length === 1 ? "The project is done." : `All ${done.length} projects are done.`}
        </p>
      )}

      {done.length > 0 && (
        <Collapsible
          key={`${filter ?? "all"}-${allDone}`}
          defaultOpen={filter === "merged" || allDone}
          data-testid="factory-done-group"
          className="rounded-xl border border-border bg-card/60 px-2 py-1.5"
        >
          <CollapsibleTrigger className="group flex w-full cursor-pointer items-center gap-1 rounded-md px-1 py-0.5 text-sm text-muted-foreground hover:text-foreground">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
            />
            Done <span className="tabular-nums">({done.length})</span>
          </CollapsibleTrigger>
          <CollapsibleContent className="flex flex-col pt-1">
            {done.map((project) => (
              <DoneProjectRow
                key={project.slug}
                snapshot={snapshot}
                project={project}
                nowS={nowS}
              />
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}

      {snapshot.hidden_projects > 0 && (
        <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
          <EyeOffIcon aria-hidden className="size-3.5" />
          {plural(snapshot.hidden_projects, "project")} hidden: you can’t open their project lead.
        </p>
      )}
    </div>
  );
}
