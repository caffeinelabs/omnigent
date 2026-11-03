/**
 * Why the project exists: the five-box why-chain (Evidence → Opportunity →
 * Bet → Project → Readout) with missing links hatched, the initiative it is
 * part of (one outcome several projects build), then the full stage bar with
 * its labels. Work without user evidence says so in words; while
 * discovery runs it offers to find it an opportunity and to ask for a metric.
 */

import { CompassIcon, MessageSquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ProjectView } from "@/lib/factoryApi";
import { Link, useSearchParams } from "@/lib/routing";
import { discoveryPageHref, initiativePageHref } from "../factoryRoutes";
import { StageBar } from "../ui/StageBar";
import { WhyChain } from "../ui/WhyChain";
import { type OpportunityFacts, type ProjectWhy, projectWhyChain } from "./projectModel";

const INITIATIVE_ROLE: Record<"skeleton" | "surface" | "part", string> = {
  skeleton: " · its walking skeleton",
  surface: " · faces users",
  part: "",
};

function sentence(why: ProjectWhy): string {
  const by = why.by;
  switch (why.kind) {
    case "idea":
      return `${by ? `${by}'s idea` : "An agent's idea"}, counted as a hunch. No user evidence. ${why.measured ? "Measured after release." : "Not measured."}`;
    case "requested":
      return `Requested${by ? ` by ${by}` : ""}. No user evidence. ${why.measured ? "Measured after release." : "Not measured."}`;
    case "stopped":
      return why.text;
    case "evidence":
      return `From user evidence${why.opportunity ? ` (${why.opportunity})` : ""}. ${why.measured ? "Measured after release." : "Not measured yet."}`;
    default:
      return "";
  }
}

export function WhyStrip({
  factoryId,
  project,
  why,
  opportunity,
  discoveryOn,
}: {
  factoryId: string;
  project: ProjectView;
  why: ProjectWhy;
  opportunity: OpportunityFacts | null;
  /** Discovery runs in this factory (its levers only make sense then). */
  discoveryOn: boolean;
}) {
  const [, setParams] = useSearchParams();
  const links = projectWhyChain(project, {
    opportunity,
    opportunityHref: why.opportunity
      ? `${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(why.opportunity)}`
      : null,
  });
  const askAtlas = () =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("room", "factory");
        return next;
      },
      { replace: true },
    );
  const said = sentence(why);
  return (
    <section
      aria-labelledby="project-why-heading"
      data-testid="project-why"
      data-kind={why.kind}
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h2
          id="project-why-heading"
          className="text-xs font-semibold tracking-wide text-muted-foreground uppercase"
        >
          Why it exists
        </h2>
        {said && why.kind !== "stopped" && (
          <p data-testid="project-why-sentence" className="text-sm text-muted-foreground">
            {said}
          </p>
        )}
        {project.initiative && (
          <p data-testid="project-why-initiative" className="text-sm text-muted-foreground">
            Part of{" "}
            <Link
              to={initiativePageHref(factoryId, project.initiative.ref)}
              componentId="factory.project.why_initiative"
              className="text-foreground underline decoration-dotted underline-offset-2"
            >
              {project.initiative.ref} {project.initiative.title}
            </Link>
            {INITIATIVE_ROLE[project.initiative.role]}
          </p>
        )}
        <span className="grow" />
        {discoveryOn && why.kind !== "evidence" && why.kind !== "stopped" && (
          <span className="flex flex-wrap items-center gap-1.5">
            <Button asChild variant="outline" size="sm">
              <Link to={discoveryPageHref(factoryId)} componentId="factory.project.why_link_opp">
                <CompassIcon aria-hidden />
                Link an opportunity
              </Link>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={askAtlas}
              componentId="factory.project.why_add_metric"
            >
              <MessageSquareIcon aria-hidden />
              Add a metric
            </Button>
          </span>
        )}
      </div>
      <WhyChain links={links} label={`Why ${project.slug} exists`} />
      <StageBar stage={project.stage} broken={project.overlay === "failed"} labels />
    </section>
  );
}
