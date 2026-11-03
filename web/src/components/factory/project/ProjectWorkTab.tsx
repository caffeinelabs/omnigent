/**
 * The project's Work tab (new cockpit): its tasks with owner and state, its
 * pull requests, demos (`#demos`), previews and env, and loops, then links to
 * the operator views one level down (the task graph, the timeline, mail and
 * activity in Debug).
 */

import { ChevronRightIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { FactorySnapshot, ProjectView, TaskState, TaskView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { projectPrList } from "../board/boardModel";
import { debugPageHref, prPageHref } from "../factoryRoutes";
import { ProjectLoops } from "../loops/LoopSections";
import { SECTION_SCROLL_MARGIN, TasksSection } from "../projects/ProjectSections";
import { findPr } from "../shell/shellRoutes";
import { FOCUS_RING } from "../ui/statusTokens";
import { ProjectDemos } from "./ProjectDemos";
import { ProjectPreviews } from "./ProjectPreviews";
import { ProjectPrs } from "./ProjectPrs";
import { useProjectTasks } from "./projectQueries";

const STATE_WORD: Record<TaskState, string> = {
  planned: "Planned",
  ready: "Ready",
  running: "Running",
  review: "In review",
  blocked: "Blocked",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

function WorkSection({
  id,
  title,
  meta,
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-heading`}
      data-testid={`project-work-${id}`}
      className="flex min-w-0 flex-col gap-3"
      style={{ scrollMarginTop: SECTION_SCROLL_MARGIN }}
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <h2 id={`${id}-heading`} className="text-base font-semibold">
          {title}
        </h2>
        {meta && <span className="text-sm text-muted-foreground">{meta}</span>}
      </div>
      {children}
    </section>
  );
}

function ownerOf(task: TaskView, snapshot: FactorySnapshot): string | null {
  const sid = task.session_ids.at(-1);
  if (!sid) return null;
  const member = snapshot.members.find((m) => m.session_id === sid);
  return member ? (member.handle ?? member.title) : null;
}

function TaskList({ snapshot, project }: { snapshot: FactorySnapshot; project: ProjectView }) {
  const tasks = useProjectTasks(snapshot.factory.id, project.slug);
  const rows = (tasks.data ?? []).filter((t) => t.state !== "cancelled");
  if (tasks.isPending) {
    return <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-muted" />;
  }
  // An older server (or a failed read): the snapshot's counts still say how it goes.
  if (tasks.error || rows.length === 0) return <TasksSection project={project} />;
  return (
    <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card">
      <table data-testid="project-task-list" className="w-full min-w-0 text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Task</th>
            <th className="px-3 py-2 font-medium">Who</th>
            <th className="px-3 py-2 font-medium">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((task) => (
            <tr key={task.id} className="border-b border-border/60 last:border-0">
              <td className="max-w-0 px-3 py-2">
                <span className="mr-1.5 font-mono text-xs text-muted-foreground">{task.ref}</span>
                <span className="break-words">{task.title}</span>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-muted-foreground">
                {ownerOf(task, snapshot) ?? "—"}
              </td>
              <td className="px-3 py-2 whitespace-nowrap">
                {STATE_WORD[task.state]}
                {task.waiting_on.length > 0 && (
                  <span className="text-muted-foreground">
                    {" "}
                    · waits on {task.waiting_on.join(", ")}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProjectWorkTab({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const navigate = useNavigate();
  const fid = snapshot.factory.id;
  const prs = projectPrList(snapshot, project);
  const onReview = (number: number) =>
    navigate(prPageHref(fid, findPr(snapshot.prs, String(number))?.id ?? number));
  const deeper: [string, string][] = [
    ["Task graph and waves", debugPageHref(fid, "graph", { params: { scope: project.slug } })],
    ["Timeline of this team", debugPageHref(fid, "timeline", { params: { focus: project.slug } })],
    ["Mail and activity", debugPageHref(fid, "activity")],
  ];
  return (
    <div data-testid="project-work" className="flex min-w-0 flex-col gap-8">
      <WorkSection id="tasks" title="Tasks" meta="owner and state">
        <TaskList snapshot={snapshot} project={project} />
      </WorkSection>
      <WorkSection id="prs" title="Pull requests">
        <ProjectPrs snapshot={snapshot} project={project} nowS={nowS} onReview={onReview} />
      </WorkSection>
      <WorkSection id="demos" title="Demos" meta="every recorded run, newest first">
        <ProjectDemos
          factoryId={fid}
          project={project}
          prs={prs}
          members={snapshot.members}
          nowS={nowS}
        />
      </WorkSection>
      <WorkSection id="previews" title="Previews and env">
        <ProjectPreviews snapshot={snapshot} project={project} nowS={nowS} />
      </WorkSection>
      <WorkSection id="loops" title="Loops">
        <ProjectLoops snapshot={snapshot} project={project} nowS={nowS} />
      </WorkSection>
      <WorkSection id="deeper" title="Deeper" meta="operator views; they keep today's detail">
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {deeper.map(([label, href]) => (
            <li key={label}>
              <Link
                to={href}
                componentId="factory.project.work_deeper"
                className={cn(
                  "inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline",
                  FOCUS_RING,
                )}
              >
                {label}
                <ChevronRightIcon aria-hidden className="size-3.5" />
              </Link>
            </li>
          ))}
        </ul>
      </WorkSection>
    </div>
  );
}
