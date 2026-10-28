/**
 * The project page's sections that are new with the page: the anchored
 * section nav, Progress (stage, tasks, spend, what it waits on, the env and
 * previews), Tasks, Team (the fleet filtered to the project), Loops and the
 * Room link. Charter, PRs, Demos, Previews and Activity reuse the project
 * components.
 */

import { type ReactNode, useEffect, useRef } from "react";
import { CircleCheckIcon, ListTodoIcon, MessagesSquareIcon, TriangleAlertIcon } from "lucide-react";
import type { FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ageText, prNoEnvReason, projectPrList } from "../board/boardModel";
import { devEnvStatus } from "../board/DevEnvPill";
import { HarnessGlyph, MemberStatusDot, ROLE_NAME, statusWord } from "../board/TeamStrip";
import {
  TONE_DOT,
  TONE_TEXT,
  formatElapsed,
  formatUsd,
  plural,
  previewStatus,
  shortSha,
} from "../factoryFormat";
import { type ProjectSection, agentPageHref, prPageHref, roomPageHref } from "../factoryRoutes";
import { EmptyState, SectionCard } from "../project/projectParts";
import { projectDecisions, projectTeam } from "../project/projectModel";
import { PAGE_EXTRA_TOP } from "../shell/scroll";
import { StageDots } from "./StageDots";

export const SECTION_LABEL: Record<ProjectSection, string> = {
  overview: "Overview",
  tasks: "Tasks",
  prs: "PRs",
  demos: "Demos",
  previews: "Previews & env",
  decisions: "Decisions",
  team: "Team",
  loops: "Loops",
  activity: "Activity",
};

/** Where a jumped-to section's top lands: under the header and the stuck nav. */
const SECTION_SCROLL_MARGIN =
  "calc(var(--omnigent-header-height) + var(--omnigent-inset-top) + 3.5rem)";

/** The page's section links (anchors), with counts; scrolls sideways on a phone. */
export function SectionNav({
  sections,
  counts,
  active,
  room,
}: {
  sections: readonly ProjectSection[];
  counts: Partial<Record<ProjectSection, number>>;
  active: ProjectSection;
  /** The Room thread link at the end. */
  room: { href: string; label: string };
}) {
  // On a phone the links scroll sideways: keep the active one in view.
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = strip.current;
    const link = box?.querySelector<HTMLElement>(`[data-testid="project-nav-${active}"]`);
    if (!box || !link) return;
    if (link.offsetLeft < box.scrollLeft) box.scrollLeft = link.offsetLeft - 16;
    else if (link.offsetLeft + link.offsetWidth > box.scrollLeft + box.clientWidth) {
      box.scrollLeft = link.offsetLeft + link.offsetWidth - box.clientWidth + 16;
    }
  }, [active]);
  return (
    <nav
      aria-label="Project sections"
      data-testid="project-section-nav"
      className="sticky z-10 -mx-4 border-b border-border bg-background/95 px-4 backdrop-blur-md md:mx-0 md:px-0"
      // The page scroller's padding already clears the header; this takes back
      // its extra 1.5rem so the nav sits right under the header.
      style={{ top: `calc(-1 * ${PAGE_EXTRA_TOP})` }}
    >
      <div
        ref={strip}
        className="relative flex min-w-0 items-center gap-3 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {sections.map((section) => (
          <a
            key={section}
            href={`#${section}`}
            data-testid={`project-nav-${section}`}
            aria-current={active === section ? "true" : undefined}
            className={cn(
              "relative inline-flex h-9 shrink-0 items-center gap-1.5 text-ui font-medium whitespace-nowrap",
              "after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:rounded-full after:bg-foreground after:opacity-0",
              active === section
                ? "text-foreground after:opacity-100"
                : "text-foreground/60 hover:text-foreground dark:text-muted-foreground dark:hover:text-foreground",
            )}
          >
            {SECTION_LABEL[section]}
            {counts[section] != null && counts[section] > 0 && (
              <span className="rounded-full bg-muted px-1.5 text-xs leading-4 font-normal text-muted-foreground tabular-nums">
                {counts[section]}
              </span>
            )}
          </a>
        ))}
        <Link
          to={room.href}
          componentId="factory.project.room"
          data-testid="project-nav-room"
          className="inline-flex h-9 shrink-0 items-center gap-1.5 text-ui font-medium whitespace-nowrap text-foreground/60 hover:text-foreground dark:text-muted-foreground"
        >
          <MessagesSquareIcon aria-hidden className="size-3.5" />
          Room {room.label}
        </Link>
      </div>
    </nav>
  );
}

/** One anchored section: the heading row is its own, the body the caller's. */
export function PageSection({
  id,
  title,
  meta,
  action,
  children,
  className,
}: {
  id: ProjectSection;
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      aria-label={title}
      data-testid={`project-section-${id}`}
      className={cn("flex min-w-0 flex-col gap-3", className)}
      style={{ scrollMarginTop: SECTION_SCROLL_MARGIN }}
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
        <h2 className="text-base font-semibold">{title}</h2>
        {meta && <span className="text-sm text-muted-foreground">{meta}</span>}
        {action && <div className="ml-auto flex items-center gap-2">{action}</div>}
      </div>
      {children}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[6.5rem_minmax(0,1fr)] items-baseline gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** Where the project stands: stage, tasks, spend, what it waits on, env and previews. */
export function ProgressCard({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const tasks = project.tasks_summary;
  const waiting = projectDecisions(snapshot, project).filter((d) => d.status === "open");
  const env = project.env;
  const envStatus = env ? devEnvStatus(env) : null;
  const prs = projectPrList(snapshot, project).filter((pr) => pr.state === "open");
  const loops = project.loops_summary;
  return (
    <SectionCard title="Progress" testId="project-progress" className="gap-2.5">
      <dl className="flex flex-col gap-2">
        <Fact label="Stage">
          <StageDots project={project} nowS={nowS} />
        </Fact>
        <Fact label="Tasks">
          {tasks && tasks.total > 0 ? (
            <span className="tabular-nums">
              {tasks.done}/{tasks.total} done
              {tasks.ready > 0 && ` · ${tasks.ready} ready`}
              {tasks.blocked > 0 && (
                <span className="text-warning"> · {tasks.blocked} blocked</span>
              )}
              {tasks.critical_path_min != null && (
                <span className="text-muted-foreground">
                  {" "}
                  · critical path {formatElapsed(tasks.critical_path_min * 60)}
                </span>
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">no work graph yet</span>
          )}
        </Fact>
        <Fact label="Spend">
          <span className="font-mono tabular-nums">{formatUsd(project.cost_usd) ?? "—"}</span>
        </Fact>
        <Fact label="Waiting on">
          {waiting.length === 0 ? (
            <span className="inline-flex items-center gap-1 text-muted-foreground">
              <CircleCheckIcon aria-hidden className="size-3.5 text-success" />
              nobody
            </span>
          ) : (
            <span className="flex flex-wrap gap-x-2 gap-y-1">
              {waiting.map((d) => (
                <a
                  key={d.id}
                  href="#decisions"
                  className={cn(
                    "font-mono hover:underline",
                    d.blocking ? "text-warning" : "text-foreground",
                  )}
                  title={d.question}
                >
                  {d.ref}
                  <span className="font-sans text-muted-foreground">
                    {" "}
                    · {ageText(nowS - d.created_at).replace(" ago", "")}
                  </span>
                </a>
              ))}
            </span>
          )}
        </Fact>
        <Fact label="Claim ≠ gates">
          {project.claim_mismatch ? (
            <span className="inline-flex items-center gap-1 text-warning">
              <TriangleAlertIcon aria-hidden className="size-3.5" />
              the PL claims {project.agent_stage}
            </span>
          ) : (
            <span className="text-muted-foreground">no</span>
          )}
        </Fact>
        {loops && (
          <Fact label="Loops">
            <span className="font-mono tabular-nums">
              {loops.running}▶ · {loops.converged}✓
              {loops.exhausted > 0 && <span className="text-warning"> · {loops.exhausted}⛔</span>}
            </span>
          </Fact>
        )}
        <Fact label="Env">
          {envStatus ? (
            <span className="inline-flex flex-wrap items-center gap-1.5">
              <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[envStatus.tone])} />
              {env?.reported?.deployed.sha ? (
                <span className="font-mono">
                  {env.reported.deployed.ref ? `${env.reported.deployed.ref}@` : ""}
                  {shortSha(env.reported.deployed.sha)}
                </span>
              ) : (
                envStatus.word
              )}
              {env?.reported?.urls.app && (
                <a
                  href={env.reported.urls.app}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  app ↗
                </a>
              )}
              {env?.reported?.urls.api && (
                <a
                  href={env.reported.urls.api}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  api ↗
                </a>
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">
              {project.charter?.env_needed === false ? "not needed" : "not up yet"}
            </span>
          )}
        </Fact>
        {prs.length > 0 && (
          <Fact label="Previews">
            <span className="flex flex-wrap gap-x-3 gap-y-1">
              {prs.map((pr) => {
                if (prNoEnvReason(pr, project)) return null;
                const status = previewStatus(pr.preview, pr.head_sha);
                return (
                  <Link
                    key={pr.id}
                    to={prPageHref(fid, pr.id)}
                    componentId="factory.project.progress_preview"
                    className="inline-flex items-center gap-1.5 hover:underline"
                  >
                    <span
                      aria-hidden
                      className={cn("size-1.5 rounded-full", TONE_DOT[status.tone])}
                    />
                    <span className="font-mono">p{pr.number}</span>
                    <span className={TONE_TEXT[status.tone]}>{status.label}</span>
                  </Link>
                );
              })}
            </span>
          </Fact>
        )}
      </dl>
    </SectionCard>
  );
}

/** The project's work graph (tasks in waves), once the PL registers tasks. */
export function TasksSection({ project }: { project: ProjectView }) {
  const tasks = project.tasks_summary;
  if (!tasks || tasks.total === 0) {
    return (
      <EmptyState
        testId="project-tasks-empty"
        icon={<ListTodoIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title="No tasks registered yet"
        body="The PL’s tasks show here as a small work graph: waves left to right, the critical path, what is ready and what is blocked."
      />
    );
  }
  const cells: [string, number, string][] = [
    ["done", tasks.done, "text-success"],
    ["running", tasks.running, "text-session-active"],
    ["ready", tasks.ready, "text-foreground"],
    ["blocked", tasks.blocked, "text-warning"],
    ["failed", tasks.failed, "text-destructive"],
  ];
  return (
    <div
      data-testid="project-tasks-summary"
      className="flex flex-wrap gap-x-6 gap-y-2 rounded-xl border border-border bg-card px-4 py-3 text-sm"
    >
      {cells.map(([label, n, tone]) => (
        <span key={label} className="inline-flex items-baseline gap-1.5">
          <span
            className={cn(
              "text-lg font-semibold tabular-nums",
              n > 0 ? tone : "text-muted-foreground",
            )}
          >
            {n}
          </span>
          <span className="text-muted-foreground">{label}</span>
        </span>
      ))}
      <span className="text-muted-foreground">of {plural(tasks.total, "task")}</span>
    </div>
  );
}

/**
 * Every agent on the project: PL first, what each is doing, harness, spend
 * and last activity; with `factoryId` each opens its Agent page.
 */
export function TeamTable({
  project,
  nowS,
  factoryId,
}: {
  project: ProjectView;
  nowS: number;
  factoryId?: string;
}) {
  const team = projectTeam(project);
  if (team.length === 0) {
    return (
      <EmptyState
        testId="project-team-empty"
        title="No agents yet"
        body="Atlas spawns the project lead; the PL brings its team."
      />
    );
  }
  return (
    <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-card">
      <table data-testid="project-team" className="w-full min-w-0 text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Agent</th>
            <th className="px-3 py-2 font-medium max-md:hidden">Role</th>
            <th className="px-3 py-2 font-medium">State</th>
            <th className="px-3 py-2 text-right font-medium">$</th>
            <th className="px-3 py-2 text-right font-medium max-sm:hidden">Last</th>
          </tr>
        </thead>
        <tbody>
          {team.map((member) => (
            <tr
              key={member.session_id}
              data-testid={`project-member-${member.session_id}`}
              className="border-b border-border/60 last:border-0"
            >
              <td className="max-w-0 px-3 py-2">
                <span className="flex min-w-0 items-center gap-2">
                  <MemberStatusDot member={member} />
                  <HarnessGlyph harness={member.harness} className="size-3.5 shrink-0" />
                  <Link
                    to={factoryId ? agentPageHref(factoryId, member.session_id) : member.url}
                    componentId="factory.project.member"
                    className="min-w-0 truncate font-medium hover:underline"
                  >
                    {member.title}
                  </Link>
                </span>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-muted-foreground max-md:hidden">
                {member.role ? ROLE_NAME[member.role] : "Agent"}
              </td>
              <td className="max-w-0 px-3 py-2">
                <span
                  className="block truncate text-muted-foreground"
                  title={member.summary ?? undefined}
                >
                  <span className={member.running ? "text-session-active" : undefined}>
                    {statusWord(member)}
                  </span>
                  {member.summary ? ` · ${member.summary}` : ""}
                </span>
              </td>
              <td className="px-3 py-2 text-right font-mono tabular-nums">
                {formatUsd(member.cost_usd) ?? "—"}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap text-muted-foreground max-sm:hidden">
                {member.last_activity_at != null ? ageText(nowS - member.last_activity_at) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The project's Room thread, linked from the page. */
export function roomLink(factoryId: string, project: ProjectView): { href: string; label: string } {
  return { href: roomPageHref(factoryId, `project:${project.slug}`), label: `#${project.slug}` };
}
