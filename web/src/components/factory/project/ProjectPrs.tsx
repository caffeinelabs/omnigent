/**
 * The project page's PRs section: every PR of the project with its gates, Sentinel
 * and human review, preview, demo and the Review action (it opens the PR
 * page). Open PRs first; merged and closed ones fold to a line.
 */

import { GitPullRequestIcon } from "lucide-react";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { projectPrList } from "../board/boardModel";
import { PrRow } from "../board/PrRow";
import { plural } from "../factoryFormat";
import { ReadyForMergeBanner } from "../review/ReadyForMergeBanner";
import { EmptyState, SectionCard } from "./projectParts";

export function ProjectPrs({
  snapshot,
  project,
  nowS,
  onReview,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
  onReview: (number: number) => void;
}) {
  const prs = projectPrList(snapshot, project);
  const open = prs.filter((pr) => pr.state === "open");
  const done = prs.filter((pr) => pr.state !== "open");
  const merged = done.filter((pr) => pr.state === "merged").length;
  const paused = project.overlay === "paused";
  if (prs.length === 0) {
    return (
      <EmptyState
        testId="project-prs-empty"
        icon={<GitPullRequestIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title="No PRs yet"
        body={
          project.stage === "planning" || project.stage === "queued"
            ? "The PL is planning; the first PR usually lands in 15–30 min."
            : "PRs the project opens show up here with their gates, preview and demo."
        }
      />
    );
  }
  return (
    <div data-testid="project-prs" className="flex min-w-0 flex-col gap-4">
      {open.length > 0 && (
        <SectionCard
          title="Open"
          meta={plural(open.length, "PR")}
          testId="project-prs-open"
          className="gap-2"
        >
          {open.map((pr) => (
            <PrRow
              key={pr.id}
              pr={pr}
              project={project}
              nowS={nowS}
              onReview={onReview}
              readyBanner={
                !paused && (
                  <ReadyForMergeBanner
                    pr={pr}
                    variant="card"
                    project={project}
                    factoryId={snapshot.factory.id}
                    onReview={onReview}
                    nowS={nowS}
                  />
                )
              }
            />
          ))}
        </SectionCard>
      )}
      {done.length > 0 && (
        <SectionCard
          title="Merged and closed"
          meta={`${merged} merged · ${done.length - merged} closed`}
          testId="project-prs-done"
          className="gap-1"
        >
          {done.map((pr) => (
            <PrRow key={pr.id} pr={pr} project={project} nowS={nowS} onReview={onReview} />
          ))}
        </SectionCard>
      )}
    </div>
  );
}
