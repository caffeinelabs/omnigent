/**
 * The project's History tab (new cockpit): its team changes (each a
 * definition version, with Restore, which stages the change in the team
 * editor), its charter changes (each a version, revertible from the Charter
 * tab), and its recent activity in plain words.
 */

import { ChevronRightIcon } from "lucide-react";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ActivityList } from "../activity/ActivityList";
import { formatElapsed } from "../factoryFormat";
import { projectTabHref } from "../factoryRoutes";
import { TeamHistory } from "../team/TeamHistory";
import type { StagedTeamChange } from "../team/teamModel";
import { stageTeamChanges, teamEditorHref } from "../team/teamStaging";
import { useProjectTeam } from "../team/useProjectTeam";
import { FOCUS_RING } from "../ui/statusTokens";
import { SectionCard } from "./projectParts";
import { useCharterVersions } from "./projectQueries";

export function ProjectHistoryTab({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const navigate = useNavigate();
  const team = useProjectTeam(snapshot, project, { history: true });
  const charter = useCharterVersions(fid, project);
  const mayEdit = team.spec != null && snapshot.viewer?.role !== "viewer";
  const restore = (change: Extract<StagedTeamChange, { kind: "restore" }>) => {
    stageTeamChanges(fid, project.slug, [change]);
    navigate(teamEditorHref(fid, project.slug));
  };
  const versions = charter.data?.versions ?? [];
  return (
    <div
      data-testid="project-history"
      className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-2"
    >
      <div className="flex min-w-0 flex-col gap-4">
        <SectionCard
          title="Team history"
          meta="each change is a definition version"
          testId="project-team-history"
        >
          {team.spec ? (
            <TeamHistory
              rows={team.history}
              current={team.current}
              nowS={nowS}
              loading={team.historyLoading}
              names={team.names}
              onRestore={mayEdit ? restore : undefined}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              This project runs today's bundle; team changes show here once it has its own team.
            </p>
          )}
        </SectionCard>
        <SectionCard
          title="Charter changes"
          meta="each change is a version you can undo or revert"
          testId="project-charter-history"
        >
          {charter.isPending ? (
            <div aria-busy="true" className="h-16 animate-pulse rounded-lg bg-muted" />
          ) : versions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {charter.error
                ? "This server keeps no charter history yet."
                : "The charter has not changed since the project started."}
            </p>
          ) : (
            <ol className="m-0 flex list-none flex-col divide-y divide-border p-0">
              {versions.slice(0, 5).map((v) => (
                <li key={v.ref} className="flex items-baseline gap-2 py-2 text-sm first:pt-0">
                  <span className="w-12 shrink-0 font-mono text-xs text-muted-foreground">
                    {v.ref}
                  </span>
                  <span className="min-w-0 grow">
                    {v.changes.length
                      ? v.changes.map((c) => c.field.replace(/_/g, " ")).join(", ")
                      : v.op}
                    <span className="block text-xs text-muted-foreground">
                      {v.by} · {formatElapsed(nowS - v.at)} ago
                      {v.status === "undone" ? " · undone" : ""}
                      {v.revert_of ? ` · reverts ${v.revert_of}` : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
          <Link
            to={projectTabHref(fid, project.slug, "charter")}
            componentId="factory.project.history_charter"
            className={cn(
              "inline-flex items-center gap-1 self-start text-sm underline-offset-2 hover:underline",
              FOCUS_RING,
            )}
          >
            The charter and every version
            <ChevronRightIcon aria-hidden className="size-3.5" />
          </Link>
        </SectionCard>
      </div>
      <SectionCard title="Recent activity" testId="project-activity">
        <ActivityList
          factoryId={fid}
          projects={snapshot.projects}
          members={snapshot.members}
          project={project.slug}
          heightClassName="max-h-[min(60vh,36rem)]"
        />
      </SectionCard>
    </div>
  );
}
