/**
 * One project on the new cockpit's Projects page, outcome first: the slug and
 * its health chip, what users get as the title, why it exists, its stage as a
 * six-segment bar with its age, the lead's latest update, its team (a ring
 * per member that opens the agent's panel, the shape and models in words) and
 * its spend against the budget. A stripe on the left says it needs you
 * (amber, its merge) or stopped (red); nothing otherwise.
 */

import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { MemberRing, ringLabel } from "../agents/MemberRing";
import type { TeamMember } from "../agents/teamsModel";
import { homeTeamLine, money } from "../home/homeModel";
import { prPageHref, projectPageHref } from "../factoryRoutes";
import { HealthChip } from "../project/HealthChip";
import { WhyLineText } from "../project/WhyLineText";
import { StageBar } from "../ui/StageBar";
import { DATA_HUE, FOCUS_RING, MEANING } from "../ui/statusTokens";
import type { ProjectCardData } from "./projectCards";

/** A card shows at most this many rings, then `+N`. */
export const CARD_RINGS_MAX = 8;

export function TeamRings({
  team,
  onAgent,
  size = 26,
  max = CARD_RINGS_MAX,
  testIdPrefix = "project-ring",
}: {
  team: readonly TeamMember[];
  onAgent: (member: TeamMember) => void;
  size?: number;
  max?: number;
  testIdPrefix?: string;
}) {
  const shown = team.slice(0, max);
  const more = team.length - shown.length;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((member) => (
        <button
          key={member.row.session_id}
          type="button"
          aria-label={ringLabel(member)}
          data-testid={`${testIdPrefix}-${member.row.handle ?? member.row.session_id}`}
          onClick={() => onAgent(member)}
          className={cn("cursor-pointer rounded-full", FOCUS_RING)}
        >
          <MemberRing member={member} size={size} />
        </button>
      ))}
      {more > 0 && <span className="text-xs text-muted-foreground">+{more}</span>}
    </div>
  );
}

export function TeamLine({ team }: { team: readonly TeamMember[] }) {
  return (
    <p className="text-xs text-muted-foreground" data-testid="project-team-line">
      {homeTeamLine(team).map((part, i) => (
        <span key={part.role}>
          {i > 0 && " · "}
          <span className="font-semibold text-foreground">{part.role}</span>{" "}
          {part.models.join(", ")}
        </span>
      ))}
    </p>
  );
}

export function SpendMeter({ spent, budget }: { spent: number; budget: number | null }) {
  const share = budget ? Math.min(1, spent / budget) : 0;
  return (
    <div className="flex w-28 shrink-0 flex-col gap-1" data-testid="project-card-spend">
      <span className="text-xs text-muted-foreground tabular-nums">
        {money(spent)}
        {budget ? ` of ${money(budget)}` : " spent"}
      </span>
      {budget ? (
        <span
          aria-hidden
          className="relative block h-1.5 w-full overflow-hidden rounded-full bg-muted"
        >
          <span
            className="absolute inset-y-0 left-0 rounded-full"
            style={{ width: `${share * 100}%`, background: DATA_HUE }}
          />
        </span>
      ) : null}
    </div>
  );
}

export function ProjectCard({
  factoryId,
  card,
  onAgent,
}: {
  factoryId: string;
  card: ProjectCardData;
  onAgent: (member: TeamMember) => void;
}) {
  const titleId = `project-card-${card.slug}-title`;
  return (
    <article
      aria-labelledby={titleId}
      data-testid={`project-card-${card.slug}`}
      data-stripe={card.stripe}
      data-why={card.origin.kind}
      className="relative flex min-w-0 flex-col gap-2.5 overflow-hidden rounded-xl border border-border bg-card p-4"
    >
      {card.stripe !== "none" && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px]"
          style={{ background: (card.stripe === "red" ? MEANING.broken : MEANING.needsYou).hue }}
        />
      )}
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
          {card.slug}
        </span>
        <span className="grow" />
        {card.health && (
          <HealthChip chip={card.health} testId={`project-card-${card.slug}-health`} />
        )}
      </div>
      <div className="min-w-0">
        <h2 id={titleId} className="text-[15px] leading-snug font-semibold">
          <Link
            to={projectPageHref(factoryId, card.slug)}
            componentId="factory.projects.card"
            className={cn("text-foreground underline-offset-2 hover:underline", FOCUS_RING)}
          >
            {card.title}
          </Link>
        </h2>
        <WhyLineText
          factoryId={factoryId}
          why={card.origin}
          className="mt-0.5"
          testId={`project-card-${card.slug}-why`}
        />
      </div>
      <div className="min-w-0">
        <StageBar stage={card.step} broken={card.broken} />
        <p className="mt-1 text-xs text-muted-foreground">
          <span
            className={cn("font-semibold", card.broken ? MEANING.broken.text : "text-foreground")}
          >
            {card.stageLabel}
          </span>
          {card.pr && (
            <>
              {" · "}
              <Link
                to={prPageHref(factoryId, card.pr.id)}
                componentId="factory.projects.card_pr"
                className={cn(
                  "font-mono underline decoration-dotted underline-offset-2",
                  FOCUS_RING,
                )}
              >
                {card.pr.ref}
              </Link>
            </>
          )}
          {card.stageAge && ` · ${card.stageAge}`}
        </p>
      </div>
      {card.update && (
        <p
          data-testid={`project-card-${card.slug}-update`}
          className="border-l-2 border-border pl-2 text-sm text-foreground/85"
        >
          {card.update.by && <span className="font-semibold">{card.update.by}: </span>}
          {card.update.text}
        </p>
      )}
      <div className="mt-auto flex min-w-0 items-start gap-3">
        <div className="min-w-0 grow">
          {card.team.length > 0 ? (
            <>
              <TeamRings team={card.team} onAgent={onAgent} />
              <div className="mt-1">
                <TeamLine team={card.team} />
              </div>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">No agents on it yet</p>
          )}
        </div>
        {card.spend && <SpendMeter spent={card.spend.spent} budget={card.spend.budget} />}
      </div>
    </article>
  );
}
