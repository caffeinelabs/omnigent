/**
 * Projects as a board by stage: Plan, Build, Review, Verify, Ready for merge
 * and Shipped. Each project is a small card (outcome, rings, why in short, age
 * in stage); shipped work shows what its readout says. Ready for merge says
 * "Bottleneck: your merge" once work piles up there. Stopped projects are not
 * on the board; one line under it names them. The board scrolls sideways
 * inside its own box, never the page.
 */

import { CircleAlertIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import type { TeamMember } from "../agents/teamsModel";
import { projectPageHref } from "../factoryRoutes";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import type { BoardColumn, ProjectCardData } from "./projectCards";
import { TeamRings } from "./ProjectCard";

function BoardCard({
  factoryId,
  card,
  onAgent,
}: {
  factoryId: string;
  card: ProjectCardData;
  onAgent: (member: TeamMember) => void;
}) {
  return (
    <li
      data-testid={`project-board-card-${card.slug}`}
      className="flex flex-col gap-1.5 rounded-lg border border-border bg-card p-2.5"
    >
      <Link
        to={projectPageHref(factoryId, card.slug)}
        componentId="factory.projects.board_card"
        className={cn("text-sm leading-snug font-semibold hover:underline", FOCUS_RING)}
      >
        {card.title}
      </Link>
      {card.team.length > 0 && (
        <TeamRings
          team={card.team}
          onAgent={onAgent}
          size={20}
          max={6}
          testIdPrefix="project-board-ring"
        />
      )}
      <p className="text-xs text-muted-foreground">
        {card.origin.kind === "evidence" && card.origin.opportunity
          ? `from ${card.origin.opportunity}`
          : "no user evidence"}
        {card.stageAge ? ` · ${card.stageAge}` : ""}
      </p>
    </li>
  );
}

export function ProjectBoard({
  factoryId,
  columns,
  stopped,
  onAgent,
}: {
  factoryId: string;
  columns: readonly BoardColumn[];
  stopped: readonly ProjectCardData[];
  onAgent: (member: TeamMember) => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div
        data-testid="project-board"
        className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card/40 p-2"
      >
        <div className="grid min-w-[60rem] grid-cols-6 gap-2">
          {columns.map((column) => {
            const count = column.cards.length + column.shipped.length;
            return (
              <section
                key={column.step}
                aria-labelledby={`project-board-${column.step}`}
                data-testid={`project-board-column-${column.step}`}
                data-bottleneck={column.bottleneck || undefined}
                className={cn(
                  "flex min-w-0 flex-col gap-2 rounded-lg p-1.5",
                  column.bottleneck && cn("border", MEANING.needsYou.border),
                )}
              >
                <h2
                  id={`project-board-${column.step}`}
                  className="flex items-center gap-1.5 px-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase"
                >
                  {column.label}
                  <span className="rounded-full bg-muted px-1.5 font-normal text-foreground tabular-nums">
                    {count}
                  </span>
                </h2>
                {column.bottleneck && (
                  <p className={cn("px-1 text-xs", MEANING.needsYou.text)}>
                    Bottleneck: your merge
                  </p>
                )}
                {count === 0 ? (
                  <p className="px-1 text-xs text-muted-foreground">Empty</p>
                ) : (
                  <ul className="m-0 flex list-none flex-col gap-2 p-0">
                    {column.cards.map((card) => (
                      <BoardCard
                        key={card.slug}
                        factoryId={factoryId}
                        card={card}
                        onAgent={onAgent}
                      />
                    ))}
                    {column.shipped.map((s) => (
                      <li
                        key={s.slug}
                        data-testid={`project-board-shipped-${s.slug}`}
                        className="flex flex-col gap-1 rounded-lg border border-border bg-card p-2.5"
                      >
                        <Link
                          to={projectPageHref(factoryId, s.slug)}
                          componentId="factory.projects.board_shipped"
                          className={cn("text-sm font-semibold hover:underline", FOCUS_RING)}
                        >
                          {s.title}
                        </Link>
                        <span className="text-xs text-muted-foreground">{s.readout}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      </div>
      {stopped.length > 0 && (
        <p
          data-testid="project-board-stopped"
          className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-sm"
        >
          <CircleAlertIcon aria-hidden className={cn("size-4 shrink-0", MEANING.broken.text)} />
          <span>
            {stopped.map((card, i) => (
              <span key={card.slug}>
                {i > 0 && ", "}
                <Link
                  to={projectPageHref(factoryId, card.slug)}
                  componentId="factory.projects.board_stopped"
                  className={cn("font-mono underline underline-offset-2", FOCUS_RING)}
                >
                  {card.slug}
                </Link>
              </span>
            ))}{" "}
            stopped. Not on the board.
          </span>
        </p>
      )}
    </div>
  );
}
