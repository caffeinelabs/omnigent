/**
 * Home's Projects card: one row per live project, written in product terms.
 * Each row carries a health stripe only when it needs a person (amber: its
 * merge) or stopped (red), the outcome as its title, why it exists, its stage
 * as a six-segment bar with its age, and its team: a ring per member (role
 * glyph, state ring, vendor badge; each opens that agent's panel) with the
 * team's shape and models in words under it. The header's agent counts are
 * filters that open Teams; shipped work folds into one line.
 */

import { ChevronRightIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { MemberRing, STATE_COLOR, ringLabel } from "../agents/MemberRing";
import {
  FILTER_WORD,
  STATE_FILTERS,
  type StateCounts,
  type TeamMember,
} from "../agents/teamsModel";
import {
  discoveryPageHref,
  prPageHref,
  projectPageHref,
  projectsPageHref,
  teamsPageHref,
} from "../factoryRoutes";
import { StageBar } from "../ui/StageBar";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { type HomeProjectRow, type ShippedLine, homeTeamLine } from "./homeModel";

/** A row shows at most this many rings, then `+N`. */
export const RINGS_MAX = 7;

function Why({ factoryId, row }: { factoryId: string; row: HomeProjectRow }) {
  const { why } = row;
  if (why.kind === "stopped") {
    return <p className={cn("text-xs", MEANING.broken.text)}>{why.text}</p>;
  }
  if (why.kind === "evidence" && why.ref) {
    const [, rest] = why.text.split(why.ref);
    return (
      <p className="text-xs text-muted-foreground">
        from{" "}
        <Link
          to={`${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(why.ref)}`}
          componentId="factory.home.projects.opportunity"
          className={cn("font-mono underline decoration-dotted underline-offset-2", FOCUS_RING)}
        >
          {why.ref}
        </Link>
        {rest}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {why.noEvidence && (
        <span
          aria-hidden
          className="inline-block w-3 shrink-0 border-t-2 border-dotted border-muted-foreground/70"
        />
      )}
      <span>{why.text}</span>
    </p>
  );
}

function Rings({
  team,
  onAgent,
}: {
  team: readonly TeamMember[];
  onAgent: (m: TeamMember) => void;
}) {
  const shown = team.slice(0, RINGS_MAX);
  const more = team.length - shown.length;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((member) => (
        <button
          key={member.row.session_id}
          type="button"
          aria-label={ringLabel(member)}
          data-testid={`home-ring-${member.row.handle ?? member.row.session_id}`}
          onClick={() => onAgent(member)}
          className={cn("cursor-pointer rounded-full", FOCUS_RING)}
        >
          <MemberRing member={member} size={26} />
        </button>
      ))}
      {more > 0 && <span className="text-xs text-muted-foreground">+{more}</span>}
    </div>
  );
}

function ProjectRow({
  factoryId,
  row,
  onAgent,
}: {
  factoryId: string;
  row: HomeProjectRow;
  onAgent: (m: TeamMember) => void;
}) {
  return (
    <li
      data-testid={`home-project-${row.slug}`}
      data-stripe={row.stripe}
      className="relative grid gap-x-4 gap-y-2 border-t border-border px-4 py-2.5 first:border-t-0 lg:grid-cols-[minmax(0,1fr)_9.25rem_minmax(0,18rem)]"
    >
      {row.stripe !== "none" && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px]"
          style={{ background: (row.stripe === "red" ? MEANING.broken : MEANING.needsYou).hue }}
        />
      )}
      <div className="min-w-0">
        <Link
          to={projectPageHref(factoryId, row.slug)}
          componentId="factory.home.projects.row"
          className={cn(
            "text-sm font-semibold text-foreground underline-offset-2 hover:underline",
            FOCUS_RING,
          )}
        >
          {row.title}
        </Link>
        <Why factoryId={factoryId} row={row} />
      </div>
      <div className="min-w-0">
        <StageBar stage={row.step} broken={row.broken} />
        <p className="mt-1 text-xs text-muted-foreground">
          <span
            className={cn("font-semibold", row.broken ? MEANING.broken.text : "text-foreground")}
          >
            {row.stageLabel}
          </span>
          {row.pr && (
            <>
              {" · "}
              <Link
                to={prPageHref(factoryId, row.pr.id)}
                componentId="factory.home.projects.pr"
                className={cn(
                  "font-mono underline decoration-dotted underline-offset-2",
                  FOCUS_RING,
                )}
              >
                {row.pr.ref}
              </Link>
            </>
          )}
          {row.stageAge && ` · ${row.stageAge}`}
        </p>
      </div>
      <div className="min-w-0">
        {row.team.length > 0 ? (
          <>
            <Rings team={row.team} onAgent={onAgent} />
            <p className="mt-1 text-xs text-muted-foreground" data-testid="home-team-line">
              {homeTeamLine(row.team).map((part, i) => (
                <span key={part.role}>
                  {i > 0 && " · "}
                  <span className="font-semibold text-foreground">{part.role}</span>{" "}
                  {part.models.join(", ")}
                </span>
              ))}
            </p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">No agents on it yet</p>
        )}
      </div>
    </li>
  );
}

export function ProjectRows({
  factoryId,
  rows,
  shipped,
  counts,
  onAgent,
  className,
}: {
  factoryId: string;
  rows: readonly HomeProjectRow[];
  shipped: readonly ShippedLine[];
  counts: StateCounts;
  onAgent: (m: TeamMember) => void;
  className?: string;
}) {
  return (
    <section
      aria-labelledby="home-projects-h"
      data-testid="home-projects"
      className={cn("rounded-xl border border-border bg-card", className)}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 pb-2">
        <h2 id="home-projects-h" className="grow text-base font-semibold">
          Projects <span className="font-medium text-muted-foreground">{rows.length}</span>
        </h2>
        <nav aria-label="Agents by state" className="flex flex-wrap items-center gap-x-1 gap-y-1">
          {STATE_FILTERS.map((state) => (
            <Link
              key={state}
              to={teamsPageHref(factoryId, "teams", { state })}
              componentId="factory.home.projects.state"
              data-testid={`home-state-${state}`}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted",
                FOCUS_RING,
              )}
            >
              <span
                aria-hidden
                className="inline-block size-2 rounded-full"
                style={{
                  background:
                    state === "idle" || state === "stuck" ? "transparent" : STATE_COLOR[state],
                  border: `1.5px ${state === "stuck" ? "dashed" : "solid"} ${STATE_COLOR[state]}`,
                }}
              />
              <span className="font-semibold text-foreground tabular-nums">{counts[state]}</span>
              {FILTER_WORD[state]}
            </Link>
          ))}
        </nav>
        <Link
          to={projectsPageHref(factoryId)}
          componentId="factory.home.projects.all"
          className={cn(
            "flex items-center gap-0.5 rounded text-xs font-medium text-muted-foreground hover:text-foreground",
            FOCUS_RING,
          )}
        >
          All projects
          <ChevronRightIcon aria-hidden className="size-3.5" />
        </Link>
      </header>
      {rows.length === 0 ? (
        <p
          data-testid="home-projects-empty"
          className="border-t border-border px-4 py-3 text-sm text-muted-foreground"
        >
          No project is in progress. Add one, or let discovery propose one.
        </p>
      ) : (
        <ul className="m-0 list-none border-t border-border p-0">
          {rows.map((row) => (
            <ProjectRow key={row.slug} factoryId={factoryId} row={row} onAgent={onAgent} />
          ))}
        </ul>
      )}
      {shipped.length > 0 && (
        <p
          data-testid="home-shipped"
          className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground"
        >
          <span className="font-medium text-foreground">Shipped:</span>{" "}
          {shipped
            .map((s) => `${s.title} (${s.measured ? "measured" : "not measured"})`)
            .join(", ")}
        </p>
      )}
    </section>
  );
}
